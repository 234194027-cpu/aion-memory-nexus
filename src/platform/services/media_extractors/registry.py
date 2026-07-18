from __future__ import annotations

from pathlib import Path

from src.platform.models.media_artifact import MediaArtifact
from src.platform.services.media_extractors.audio_video_transcriber import AudioVideoTranscriber
from src.platform.services.media_extractors.base import MediaExtractor, UnsupportedMediaError
from src.platform.services.media_extractors.document_converter import DocumentExtractor
from src.platform.services.media_extractors.image_ocr import ImageOcrExtractor
from src.platform.services.media_extractors.table_extractor import TableExtractor
from src.platform.services.media_extractors.text_extractor import TextExtractor


EXTRACTORS: list[MediaExtractor] = [
    TableExtractor(),
    DocumentExtractor(),
    ImageOcrExtractor(),
    AudioVideoTranscriber(),
    TextExtractor(),
]


def select_extractor(artifact: MediaArtifact, path: Path) -> MediaExtractor:
    for extractor in EXTRACTORS:
        if extractor.can_extract(artifact, path):
            return extractor
    raise UnsupportedMediaError(f"unsupported_media_type:{artifact.media_type}:{path.suffix}")
