from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
import yaml
from src.shared.db.database import get_db
from src.shared.config import OBSIDIAN_VAULT_DIR
from src.memory.models.committed_memory import CommittedMemory, CommittedStatus
from src.memory.models.raw_event import RawEvent, SourceType, SensitivityLevel, VisibilityScope, ProcessingStatus
from src.memory.models.obsidian_sync_record import ObsidianSyncRecord, SyncStatus
from src.memory.schemas.obsidian import ObsidianSyncRequest, ObsidianSyncResponse
from src.shared.security.dependencies import get_current_user
from src.shared.ids.id_generator import generate_id, generate_event_id
from src.shared.utils.hash import compute_content_hash

router = APIRouter()

WINDOWS_INVALID_FILENAME_CHARS = '<>:"/\\|?*'

VAULT_STRUCTURE = {
    "decision": "20_Decisions",
    "preference": "50_Persona Hypotheses",
    "fact": "30_Projects",
    "insight": "60_Insights",
    "task": "10_Daily",
    "project_context": "30_Projects",
    "principle": "50_Persona Hypotheses",
    "correction": "20_Decisions",
    "timeline_event": "70_Timeline",
    "persona_hypothesis": "50_Persona Hypotheses",
}

def get_folder_for_memory_type(memory_type: str) -> str:
    return VAULT_STRUCTURE.get(memory_type, "00_Inbox/Candidate Memories")

def safe_markdown_filename(title: str, memory_id: str) -> str:
    clean_title = "".join("_" if ch in WINDOWS_INVALID_FILENAME_CHARS else ch for ch in (title or "Untitled"))
    clean_title = " ".join(clean_title.split()).strip(" .")[:80] or "Untitled"
    # Prevent path traversal
    clean_title = clean_title.replace("..", "").replace("/", "_").replace("\\", "_")
    suffix = (memory_id or "memory")[-8:]
    return f"{clean_title}-{suffix}.md"

def ensure_vault_structure():
    folders = [
        "00_Inbox/Candidate Memories",
        "10_Daily",
        "20_Decisions",
        "30_Projects",
        "40_People",
        "50_Persona Hypotheses",
        "60_Insights",
        "70_Timeline",
        "90_Archive",
    ]
    for folder in folders:
        (OBSIDIAN_VAULT_DIR / folder).mkdir(parents=True, exist_ok=True)

def generate_markdown_with_frontmatter(memory: dict) -> str:
    frontmatter = {
        "memory_id": memory.get("id"),
        "memory_type": memory.get("memory_type"),
        "status": memory.get("status"),
        "confidence": memory.get("confidence"),
        "importance": memory.get("importance"),
        "sensitivity": memory.get("sensitivity"),
        "project_id": memory.get("project_id"),
        "repo_id": memory.get("repo_id"),
        "workspace_id": memory.get("workspace_id"),
        "created_at": memory.get("created_at"),
        "last_sync": datetime.now(timezone.utc).isoformat(),
    }
    
    yaml_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
    return f"---\n{yaml_str}---\n\n{memory.get('body', '')}"

@router.post("/export", response_model=ObsidianSyncResponse)
async def export_to_obsidian(
    request: ObsidianSyncRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    ensure_vault_structure()
    
    query_filters = [CommittedMemory.user_id == user.id, CommittedMemory.status == CommittedStatus.ACTIVE]
    
    if request.memory_ids:
        query_filters.append(CommittedMemory.id.in_(request.memory_ids))
    
    result = await db.execute(
        select(CommittedMemory).where(*query_filters)
    )
    memories = result.scalars().all()
    
    exported_count = 0
    for memory in memories:
        folder = get_folder_for_memory_type(memory.memory_type.value)
        file_name = safe_markdown_filename(memory.title, memory.id)
        file_path = OBSIDIAN_VAULT_DIR / folder / file_name
        
        memory_dict = {
            "id": memory.id,
            "memory_type": memory.memory_type.value,
            "title": memory.title,
            "body": memory.body,
            "status": memory.status.value,
            "confidence": memory.confidence,
            "importance": memory.importance,
            "sensitivity": memory.sensitivity.value,
            "project_id": memory.project_id,
            "repo_id": memory.repo_id,
            "workspace_id": memory.workspace_id,
            "created_at": memory.created_at.isoformat() if memory.created_at else None,
        }
        
        content = generate_markdown_with_frontmatter(memory_dict)
        content_hash = compute_content_hash(content)
        
        file_path.write_text(content, encoding="utf-8")
        
        sync_result = await db.execute(
            select(ObsidianSyncRecord)
            .where(ObsidianSyncRecord.memory_id == memory.id)
            .where(ObsidianSyncRecord.user_id == user.id)
        )
        sync_record = sync_result.scalar_one_or_none()
        
        if sync_record:
            sync_record.file_path = str(file_path.relative_to(OBSIDIAN_VAULT_DIR))
            sync_record.last_exported_at = datetime.now(timezone.utc)
            sync_record.content_hash = content_hash
            sync_record.sync_status = SyncStatus.SYNCED
        else:
            new_sync_record = ObsidianSyncRecord(
                id=generate_id("sync"),
                user_id=user.id,
                memory_id=memory.id,
                vault_path=str(OBSIDIAN_VAULT_DIR),
                file_path=str(file_path.relative_to(OBSIDIAN_VAULT_DIR)),
                last_exported_at=datetime.now(timezone.utc),
                content_hash=content_hash,
                sync_status=SyncStatus.SYNCED,
            )
            db.add(new_sync_record)
        
        exported_count += 1
    
    await db.commit()
    
    return {"success": True, "exported_count": exported_count, "imported_count": 0}

@router.post("/import", response_model=ObsidianSyncResponse)
async def import_from_obsidian(
    request: ObsidianSyncRequest,
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    ensure_vault_structure()
    
    imported_count = 0
    imported_event_ids: list[str] = []
    for file in OBSIDIAN_VAULT_DIR.rglob("*.md"):
            content = file.read_text(encoding="utf-8")
            if content.startswith("---"):
                end_idx = content.find("---", 3)
                if end_idx != -1:
                    frontmatter_str = content[3:end_idx].strip()
                    try:
                        frontmatter = yaml.safe_load(frontmatter_str)
                        memory_id = frontmatter.get("memory_id")
                        
                        if memory_id:
                            sync_result = await db.execute(
                                select(ObsidianSyncRecord)
                                .where(ObsidianSyncRecord.memory_id == memory_id)
                                .where(ObsidianSyncRecord.user_id == user.id)
                            )
                            sync_record = sync_result.scalar_one_or_none()
                            
                            if sync_record:
                                current_hash = compute_content_hash(content)
                                if current_hash != sync_record.content_hash:
                                    body = content[end_idx + 3:].strip()
                                    try:
                                        sensitivity = SensitivityLevel(frontmatter.get("sensitivity", "normal"))
                                    except ValueError:
                                        sensitivity = SensitivityLevel.NORMAL
                                    from src.memory.services.event_ingestion import EventIngestionService
                                    event = (
                                        await EventIngestionService(db).append(
                                            user_id=user.id,
                                            content=body or content,
                                            source_type=SourceType.OBSIDIAN,
                                            source_id=str(file.relative_to(OBSIDIAN_VAULT_DIR)),
                                            project_id=frontmatter.get("project_id"),
                                            repo_id=frontmatter.get("repo_id"),
                                            workspace_id=frontmatter.get("workspace_id"),
                                            event_metadata={
                                            "memory_id": memory_id,
                                            "obsidian_file": str(file.relative_to(OBSIDIAN_VAULT_DIR)),
                                            "change_type": "obsidian_edit",
                                            },
                                            sensitivity=sensitivity,
                                            visibility_scope=VisibilityScope.PROJECT,
                                        )
                                    ).event
                                    sync_record.last_imported_at = datetime.now(timezone.utc)
                                    sync_record.content_hash = current_hash
                                    imported_count += 1
                                    imported_event_ids.append(event.id)
                    except yaml.YAMLError:
                        continue
    
    await db.commit()
    if imported_event_ids:
        from src.memory.services.event_ingestion import trigger_ingested_event
        for event_id in imported_event_ids:
            trigger_ingested_event(event_id)

    return {"success": True, "exported_count": 0, "imported_count": imported_count}

@router.get("/status")
async def get_sync_status(
    db: AsyncSession = Depends(get_db),
    user = Depends(get_current_user),
):
    result = await db.execute(select(ObsidianSyncRecord).where(ObsidianSyncRecord.user_id == user.id))
    records = result.scalars().all()
    
    status = {
        "total_records": len(records),
        "synced": 0,
        "pending": 0,
        "failed": 0,
    }
    
    for record in records:
        status[record.sync_status.value] += 1
    
    return status


@router.get("/vaults")
async def get_vaults(
    user = Depends(get_current_user),
):
    """获取 Obsidian 仓库信息"""
    vault_path = OBSIDIAN_VAULT_DIR
    return {
        "vaults": [
            {
                "path": str(vault_path),
                "name": vault_path.name if vault_path.exists() else "Default Vault",
                "exists": vault_path.exists(),
            }
        ] if vault_path.exists() else []
    }


@router.post("/connect")
async def connect_vault(
    request: dict,
    user = Depends(get_current_user),
):
    """连接 Obsidian 仓库"""
    OBSIDIAN_VAULT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_vault_structure()
    
    return {"status": "connected", "vault_path": str(OBSIDIAN_VAULT_DIR)}
