from sqlalchemy import Column, String, DateTime, Enum
from src.shared.db.database import Base
from enum import Enum as PyEnum

class SyncStatus(PyEnum):
    SYNCED = "synced"
    PENDING = "pending"
    FAILED = "failed"

class ObsidianSyncRecord(Base):
    __tablename__ = "obsidian_sync_records"
    
    id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, default="default", index=True)
    memory_id = Column(String, nullable=False)
    vault_path = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    last_exported_at = Column(DateTime(timezone=True), nullable=True)
    last_imported_at = Column(DateTime(timezone=True), nullable=True)
    content_hash = Column(String, nullable=True)
    sync_status = Column(Enum(SyncStatus, values_callable=lambda x: [e.value for e in x]), default=SyncStatus.PENDING)
