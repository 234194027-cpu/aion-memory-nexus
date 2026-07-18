from celery import Celery
from src.shared.config import settings

celery_app = Celery(
    "life_memory_worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    include=[
        "src.memory.tasks.memory_extraction",
        "src.platform.tasks.media_extraction",
    ],
)
