from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Rebuild Zvec vector index from database")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually writing to index",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for database queries (default: 100)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit total number of embeddings to process (0 = no limit)",
    )
    parser.add_argument(
        "--index-path",
        type=str,
        default=None,
        help="Override Zvec index path",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    asyncio.run(_run_rebuild(args))


async def _run_rebuild(args):
    try:
        from src.shared.config import settings

        if args.index_path:
            settings.ZVEC_INDEX_PATH = args.index_path

        from src.memory.services.vector_index_backend import get_vector_index_backend

        backend = get_vector_index_backend()

        if not backend.is_available():
            logger.error("Zvec index backend is not available. Please install zvec and set VECTOR_INDEX_BACKEND=zvec")
            sys.exit(1)

        logger.info(f"Zvec index path: {settings.ZVEC_INDEX_PATH}")
        logger.info(f"Batch size: {args.batch_size}")
        logger.info(f"Limit: {'unlimited' if args.limit == 0 else args.limit}")
        logger.info(f"Dry run: {args.dry_run}")

        if args.dry_run:
            await _dry_run_rebuild(args.batch_size, args.limit)
        else:
            await _actual_rebuild(backend, args.batch_size, args.limit)

    except Exception as e:
        logger.error(f"Rebuild failed: {e}", exc_info=True)
        sys.exit(1)


async def _dry_run_rebuild(batch_size: int, limit: int):
    from src.shared.db.database import async_session
    from src.memory.models.memory_embedding import MemoryEmbedding
    from src.memory.models.committed_memory import CommittedMemory, CommittedStatus

    async with async_session() as session:
        count = 0
        offset = 0

        while True:
            stmt = (
                MemoryEmbedding.__table__.select()
                .join(
                    CommittedMemory,
                    MemoryEmbedding.memory_id == CommittedMemory.id,
                )
                .where(CommittedMemory.status == CommittedStatus.ACTIVE)
                .offset(offset)
                .limit(batch_size)
            )
            result = await session.execute(stmt)
            rows = result.all()

            if not rows:
                break

            for emb_record, memory in rows:
                emb = getattr(emb_record, "embedding_vector", None)
                if emb and len(emb) == 1024:
                    count += 1

            offset += batch_size

            if limit > 0 and count >= limit:
                count = limit
                break

            if offset % (batch_size * 10) == 0:
                logger.info(f"Dry run: found {count} embeddings so far")

        logger.info(f"Dry run completed: would rebuild {count} embeddings")


async def _actual_rebuild(backend, batch_size: int, limit: int):
    from src.shared.db.database import async_session
    from src.memory.models.memory_embedding import MemoryEmbedding
    from src.memory.models.committed_memory import CommittedMemory, CommittedStatus

    async with async_session() as session:
        count = 0
        offset = 0

        while True:
            stmt = (
                MemoryEmbedding.__table__.select()
                .join(
                    CommittedMemory,
                    MemoryEmbedding.memory_id == CommittedMemory.id,
                )
                .where(CommittedMemory.status == CommittedStatus.ACTIVE)
                .offset(offset)
                .limit(batch_size)
            )
            result = await session.execute(stmt)
            rows = result.all()

            if not rows:
                break

            for emb_record, memory in rows:
                emb = getattr(emb_record, "embedding_vector", None)
                if not emb or len(emb) != 1024:
                    continue

                metadata = {
                    "memory_type": memory.memory_type.value,
                    "sensitivity": memory.sensitivity.value,
                    "importance": float(memory.importance or 0.0),
                    "user_id": memory.user_id,
                    "project_id": memory.project_id or "",
                }

                if backend.upsert(str(emb_record.memory_id), emb, metadata):
                    count += 1

            offset += batch_size

            if limit > 0 and count >= limit:
                count = limit
                break

            if offset % (batch_size * 10) == 0:
                logger.info(f"Rebuild: processed {count} embeddings")

        logger.info(f"Rebuild completed: {count} embeddings written to Zvec index")


if __name__ == "__main__":
    main()