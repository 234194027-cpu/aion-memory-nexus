from __future__ import annotations

from pathlib import Path

from src.platform.models.media_artifact import MediaArtifact
from src.platform.services.media_extractors.base import LocalExtractionInput


class TextExtractor:
    name = "text_extractor"
    version = "1"
    supported_types = {"text", "markdown", "file"}

    def can_extract(self, artifact: MediaArtifact, path: Path) -> bool:
        suffix = path.suffix.lower()
        mime_type = (artifact.mime_type or "").lower()
        return suffix in {".txt", ".md", ".markdown"} or mime_type in {"text/plain", "text/markdown"}

    async def extract(self, payload: LocalExtractionInput):
        from src.platform.services.media_ingestion import ExtractedNote

        text = _read_text(payload.path)
        title = payload.artifact.original_name or payload.path.name
        return ExtractedNote(
            title=title[:180],
            summary=_summary(text),
            text=text[:20_000],
            structured_data={"filename": payload.path.name, "characters": len(text)},
            source_url=payload.artifact.source_url or "",
            confidence=0.82 if text.strip() else 0.25,
            warnings=[] if text.strip() else ["文件没有可提取文本"],
        )


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _summary(text: str) -> str:
    compact = " ".join((text or "").split())
    return compact[:500] + ("..." if len(compact) > 500 else "")
