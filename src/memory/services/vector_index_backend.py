from typing import Protocol, List, Dict, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from src.shared.config import settings

logger = __import__("logging").getLogger(__name__)


class VectorIndexBackend(Protocol):
    def is_available(self) -> bool:
        """检查后端是否可用"""
        ...

    def upsert(self, memory_id: str, vector: List[float], metadata: Dict) -> bool:
        """插入或更新向量索引"""
        ...

    def delete(self, memory_id: str) -> bool:
        """从索引中删除向量"""
        ...

    def query(self, vector: List[float], top_k: int) -> List[Tuple[str, float]]:
        """查询相似向量，返回 (memory_id, similarity) 列表"""
        ...

    async def rebuild_from_db(
        self, session: AsyncSession, batch_size: int = 100
    ) -> int:
        """从数据库重建索引，返回重建数量"""
        ...


class NullVectorIndex:
    """空实现：不做任何操作，用于未启用向量索引时的默认返回"""

    def is_available(self) -> bool:
        return False

    def upsert(self, memory_id: str, vector: List[float], metadata: Dict) -> bool:
        return False

    def delete(self, memory_id: str) -> bool:
        return False

    def query(self, vector: List[float], top_k: int) -> List[Tuple[str, float]]:
        return []

    async def rebuild_from_db(
        self, session: AsyncSession, batch_size: int = 100
    ) -> int:
        return 0


_null_backend = NullVectorIndex()


def get_vector_index_backend() -> VectorIndexBackend:
    """根据配置返回向量索引后端实现"""
    backend_type = settings.VECTOR_INDEX_BACKEND.lower().strip()

    if backend_type == "zvec":
        try:
            from src.memory.services.zvec_index import ZvecIndex

            return ZvecIndex()
        except ImportError:
            logger.warning(
                "VECTOR_INDEX_BACKEND=zvec but zvec is not installed, falling back to null"
            )
            return _null_backend
        except Exception as e:
            logger.warning(
                f"Failed to initialize ZvecIndex: {e}, falling back to null"
            )
            return _null_backend

    return _null_backend