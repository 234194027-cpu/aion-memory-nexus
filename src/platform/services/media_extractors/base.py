from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.platform.models.media_artifact import MediaArtifact


@dataclass(frozen=True)
class LocalExtractionInput:
    artifact: MediaArtifact
    path: Path


class MediaExtractor(Protocol):
    name: str
    version: str
    supported_types: set[str]

    def can_extract(self, artifact: MediaArtifact, path: Path) -> bool:
        ...

    async def extract(self, payload: LocalExtractionInput):
        ...


class UnsupportedMediaError(RuntimeError):
    pass
