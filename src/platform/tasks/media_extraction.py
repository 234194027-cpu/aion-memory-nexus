import asyncio
import logging
import threading

from src.platform.services.media_ingestion import extract_stored_artifact
from src.shared.db.database import async_session
from src.shared.db.worker import celery_app


logger = logging.getLogger(__name__)


@celery_app.task
def extract_media_artifact(artifact_id: str) -> dict:
    return asyncio.run(_extract_media_artifact(artifact_id))


def trigger_media_extraction(artifact_id: str) -> None:
    try:
        extract_media_artifact.delay(artifact_id)
    except Exception:
        thread = threading.Thread(target=lambda: asyncio.run(_extract_media_artifact(artifact_id)))
        thread.daemon = True
        thread.start()


async def _extract_media_artifact(artifact_id: str) -> dict:
    async with async_session() as db:
        event, memory_id = await extract_stored_artifact(db, artifact_id=artifact_id)
        logger.info("media artifact extracted: artifact=%s event=%s memory=%s", artifact_id, event.id, memory_id)
        return {"artifact_id": artifact_id, "event_id": event.id, "memory_id": memory_id}
