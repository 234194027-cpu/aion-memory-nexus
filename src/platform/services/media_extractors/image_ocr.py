from __future__ import annotations

from pathlib import Path

from src.platform.models.media_artifact import MediaArtifact
from src.platform.services.media_extractors.base import LocalExtractionInput
from src.shared.config import settings


class ImageOcrExtractor:
    name = "image_ocr"
    version = "1"
    supported_types = {"image"}

    def can_extract(self, artifact: MediaArtifact, path: Path) -> bool:
        suffix = path.suffix.lower()
        mime_type = (artifact.mime_type or "").lower()
        return artifact.media_type == "image" or suffix in {".jpg", ".jpeg", ".png", ".webp"} or mime_type.startswith("image/")

    async def extract(self, payload: LocalExtractionInput):
        from src.platform.services.media_ingestion import ExtractedNote

        width, height = _image_size(payload.path)
        ocr_text, confidence, warnings = _run_rapidocr(payload.path)
        summary = _summary(ocr_text) if ocr_text else "图片已接收，但暂未识别到文字。"
        return ExtractedNote(
            title=f"图片 OCR 笔记：{payload.artifact.original_name or payload.path.name}"[:180],
            summary=summary,
            text=ocr_text[:20_000],
            structured_data={"width": width, "height": height, "ocr_engine": "rapidocr_optional"},
            source_url=payload.artifact.source_url or "",
            confidence=confidence,
            warnings=warnings,
        )


def _image_size(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image
    except Exception:
        return None, None
    try:
        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def _run_rapidocr(path: Path) -> tuple[str, float, list[str]]:
    if not settings.MEDIA_ENABLE_RAPIDOCR:
        return "", 0.28, ["rapidocr_disabled，已保存图片元数据，开启 MEDIA_ENABLE_RAPIDOCR 后可做 OCR"]
    try:
        from rapidocr import RapidOCR
    except Exception:
        return "", 0.28, ["rapidocr_not_installed，已保存图片元数据，等待 OCR 依赖接入"]

    try:
        result = RapidOCR()(str(path))
    except Exception as exc:
        return "", 0.25, [f"rapidocr_failed:{str(exc)[:160]}"]

    lines: list[str] = []
    scores: list[float] = []
    for item in _iter_ocr_items(result):
        text = item.get("text")
        score = item.get("score")
        if text:
            lines.append(str(text))
        if isinstance(score, (float, int)):
            scores.append(float(score))
    text = "\n".join(lines)
    confidence = min(0.78, max(0.35, sum(scores) / len(scores))) if scores else (0.35 if text else 0.25)
    warnings = [] if text else ["OCR 没有识别到文字"]
    return text, confidence, warnings


def _iter_ocr_items(result):
    raw = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if raw is not None:
        for index, text in enumerate(raw):
            yield {"text": text, "score": scores[index] if scores and index < len(scores) else None}
        return
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                yield {"text": item.get("text") or item.get("rec_text"), "score": item.get("score") or item.get("confidence")}
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                text = item[1]
                score = item[2] if len(item) >= 3 else None
                if isinstance(text, (list, tuple)) and text:
                    score = text[1] if len(text) >= 2 else score
                    text = text[0]
                yield {"text": text, "score": score}


def _summary(text: str) -> str:
    compact = " ".join((text or "").split())
    return compact[:500] + ("..." if len(compact) > 500 else "")
