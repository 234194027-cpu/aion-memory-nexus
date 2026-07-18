import logging
import os
from typing import List, Dict, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from src.shared.config import settings, BASE_DIR

logger = logging.getLogger(__name__)


class ZvecIndex:
    """Zvec 向量索引封装。

    所有 Zvec SDK 导入使用延迟导入，确保未安装时不影响系统启动。
    所有操作失败时记录 warning 日志并返回安全默认值。
    """

    def __init__(self):
        self._index = None
        self._available = False
        self._dim = settings.EMBEDDING_DIMENSION
        self._index_path = str(BASE_DIR / settings.ZVEC_INDEX_PATH)
        self._collection_name = settings.ZVEC_COLLECTION_NAME
        self._init_index()

    def _init_index(self):
        """延迟初始化 Zvec 索引"""
        try:
            import zvec

            os.makedirs(os.path.dirname(self._index_path), exist_ok=True)

            self._index = zvec.Index(
                path=self._index_path,
                collection=self._collection_name,
                dimension=self._dim,
                enable_fts=settings.ZVEC_ENABLE_FTS,
            )
            self._available = True
            logger.info(f"Zvec index initialized at {self._index_path}, dim={self._dim}")
        except ImportError:
            logger.warning(
                "zvec module not found, ZvecIndex will be unavailable"
            )
            self._available = False
        except Exception as e:
            logger.warning(f"Failed to initialize Zvec index: {e}")
            self._available = False

    def is_available(self) -> bool:
        return self._available and self._index is not None

    def upsert(self, memory_id: str, vector: List[float], metadata: Dict) -> bool:
        if not self.is_available():
            return False

        if len(vector) != self._dim:
            logger.warning(
                f"Zvec upsert: dimension mismatch, expected {self._dim}, got {len(vector)}"
            )
            return False

        try:
            self._index.upsert(
                id=memory_id,
                vector=vector,
                metadata=metadata,
            )
            return True
        except Exception as e:
            logger.warning(f"Zvec upsert failed for {memory_id}: {e}")
            return False

    def delete(self, memory_id: str) -> bool:
        if not self.is_available():
            return False

        try:
            self._index.delete(id=memory_id)
            return True
        except Exception as e:
            logger.warning(f"Zvec delete failed for {memory_id}: {e}")
            return False

    def query(self, vector: List[float], top_k: int) -> List[Tuple[str, float]]:
        if not self.is_available():
            return []

        if len(vector) != self._dim:
            logger.warning(
                f"Zvec query: dimension mismatch, expected {self._dim}, got {len(vector)}"
            )
            return []

        try:
            results = self._index.query(
                vector=vector,
                top_k=top_k,
            )
            return [(str(r.id), float(r.score)) for r in results]
        except Exception as e:
            logger.warning(f"Zvec query failed: {e}")
            return []

    async def rebuild_from_db(
        self, session: AsyncSession, batch_size: int = 100
    ) -> int:
        if not self.is_available():
            logger.warning("Zvec not available, skip rebuild")
            return 0

        from src.memory.models.memory_embedding import MemoryEmbedding
        from src.memory.models.committed_memory import CommittedMemory, CommittedStatus

        try:
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
                    if not emb or len(emb) != self._dim:
                        continue

                    metadata = {
                        "memory_type": memory.memory_type.value,
                        "sensitivity": memory.sensitivity.value,
                        "importance": float(memory.importance or 0.0),
                        "user_id": memory.user_id,
                        "project_id": memory.project_id or "",
                    }

                    if self.upsert(str(emb_record.memory_id), emb, metadata):
                        count += 1

                offset += batch_size
                logger.info(f"Zvec rebuild: processed {count} embeddings")

            logger.info(f"Zvec rebuild completed, total {count} embeddings")
            return count
        except Exception as e:
            logger.error(f"Zvec rebuild failed: {e}")
            return count