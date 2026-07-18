from __future__ import annotations

from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

from src.platform.models.media_artifact import MediaArtifact
from src.platform.services.media_extractors.base import LocalExtractionInput
from src.shared.config import settings


class DocumentExtractor:
    name = "document_extractor"
    version = "1"
    supported_types = {"document", "pdf", "file"}

    def can_extract(self, artifact: MediaArtifact, path: Path) -> bool:
        suffix = path.suffix.lower()
        mime_type = (artifact.mime_type or "").lower()
        return suffix in {".pdf", ".docx", ".pptx", ".html", ".htm"} or mime_type in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/html",
        }

    async def extract(self, payload: LocalExtractionInput):
        suffix = payload.path.suffix.lower()
        if settings.MEDIA_ENABLE_MARKITDOWN:
            try:
                return _extract_with_markitdown(payload)
            except RuntimeError:
                pass
        if suffix == ".pdf":
            return _extract_pdf(payload)
        if suffix == ".docx":
            return _extract_docx(payload)
        if suffix == ".pptx":
            return _extract_pptx(payload)
        if suffix in {".html", ".htm"}:
            return _extract_html(payload)
        return _extract_text_like_document(payload)


def _extract_pdf(payload: LocalExtractionInput):
    from src.platform.services.media_ingestion import ExtractedNote

    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise RuntimeError("pypdf_not_installed") from exc

    reader = PdfReader(str(payload.path))
    texts = []
    for page in reader.pages[:50]:
        texts.append(page.extract_text() or "")
    text = "\n\n".join(part.strip() for part in texts if part.strip())
    warnings = [] if text else ["PDF 没有提取到文本，可能是扫描件，需要 OCR/Docling"]
    return ExtractedNote(
        title=f"PDF 笔记：{payload.artifact.original_name or payload.path.name}"[:180],
        summary=_summary(text) if text else "PDF 已接收，但没有提取到可用文本。",
        text=text[:20_000],
        structured_data={"format": "pdf", "pages": len(reader.pages), "extracted_pages": min(len(reader.pages), 50)},
        source_url=payload.artifact.source_url or "",
        confidence=0.68 if text else 0.25,
        warnings=warnings,
    )


def _extract_with_markitdown(payload: LocalExtractionInput):
    from src.platform.services.media_ingestion import ExtractedNote

    try:
        from markitdown import MarkItDown
    except Exception as exc:
        raise RuntimeError("markitdown_not_installed") from exc

    result = MarkItDown().convert(str(payload.path))
    text = (getattr(result, "text_content", None) or "").strip()
    return ExtractedNote(
        title=f"文档笔记：{payload.artifact.original_name or payload.path.name}"[:180],
        summary=_summary(text) if text else "文档已接收，但没有提取到可用文本。",
        text=text[:20_000],
        structured_data={"format": payload.path.suffix.lower().lstrip("."), "converter": "markitdown"},
        source_url=payload.artifact.source_url or "",
        confidence=0.7 if text else 0.25,
        warnings=[] if text else ["MarkItDown 没有提取到文本"],
    )


def _extract_html(payload: LocalExtractionInput):
    from src.platform.services.media_ingestion import ExtractedNote, _extract_title, _strip_html_text

    html = payload.path.read_text(encoding="utf-8", errors="replace")
    text = _strip_html_text(html).strip()
    title = _extract_title(html) or payload.artifact.original_name or payload.path.name
    return ExtractedNote(
        title=f"HTML 文档笔记：{title}"[:180],
        summary=_summary(text) if text else "HTML 已接收，但没有提取到可用文本。",
        text=text[:20_000],
        structured_data={"format": "html", "converter": "builtin_html"},
        source_url=payload.artifact.source_url or "",
        confidence=0.62 if text else 0.25,
        warnings=[] if text else ["HTML 没有提取到正文文本"],
    )


def _extract_text_like_document(payload: LocalExtractionInput):
    from src.platform.services.media_ingestion import ExtractedNote

    text = payload.path.read_text(encoding="utf-8", errors="replace").strip()
    return ExtractedNote(
        title=f"文档笔记：{payload.artifact.original_name or payload.path.name}"[:180],
        summary=_summary(text) if text else "文档已接收，但没有提取到可用文本。",
        text=text[:20_000],
        structured_data={"format": payload.path.suffix.lower().lstrip(".") or "text", "converter": "builtin_text"},
        source_url=payload.artifact.source_url or "",
        confidence=0.6 if text else 0.25,
        warnings=[] if text else ["文档没有提取到文本"],
    )


def _extract_docx(payload: LocalExtractionInput):
    from src.platform.services.media_ingestion import ExtractedNote

    try:
        xml_text = _read_zip_text(payload.path, "word/document.xml")
    except Exception as exc:
        raise RuntimeError("docx_read_failed") from exc
    text = _extract_ooxml_text(xml_text)
    warnings = [] if text else ["DOCX 没有提取到文本"]
    return ExtractedNote(
        title=f"Word 文档笔记：{payload.artifact.original_name or payload.path.name}"[:180],
        summary=_summary(text) if text else "Word 文档已接收，但没有提取到可用文本。",
        text=text[:20_000],
        structured_data={"format": "docx", "converter": "builtin_ooxml"},
        source_url=payload.artifact.source_url or "",
        confidence=0.68 if text else 0.25,
        warnings=warnings,
    )


def _extract_pptx(payload: LocalExtractionInput):
    from src.platform.services.media_ingestion import ExtractedNote

    texts: list[str] = []
    try:
        with zipfile.ZipFile(payload.path) as package:
            slide_names = sorted(
                name for name in package.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            for slide_name in slide_names[:80]:
                texts.append(_extract_ooxml_text(package.read(slide_name).decode("utf-8", "replace")))
    except Exception as exc:
        raise RuntimeError("pptx_read_failed") from exc
    text = "\n\n".join(item for item in texts if item.strip())
    warnings = [] if text else ["PPTX 没有提取到文本"]
    return ExtractedNote(
        title=f"PPT 文档笔记：{payload.artifact.original_name or payload.path.name}"[:180],
        summary=_summary(text) if text else "PPT 文档已接收，但没有提取到可用文本。",
        text=text[:20_000],
        structured_data={"format": "pptx", "converter": "builtin_ooxml", "slides": len(texts)},
        source_url=payload.artifact.source_url or "",
        confidence=0.66 if text else 0.25,
        warnings=warnings,
    )


def _read_zip_text(path: Path, member: str) -> str:
    with zipfile.ZipFile(path) as package:
        return package.read(member).decode("utf-8", "replace")


def _extract_ooxml_text(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    paragraphs: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) in {"p", "tr"}:
            parts = [
                text_node.text or ""
                for text_node in node.iter()
                if _local_name(text_node.tag) == "t" and text_node.text
            ]
            line = " ".join(part.strip() for part in parts if part.strip())
            if line:
                paragraphs.append(line)
    if paragraphs:
        return "\n".join(paragraphs)
    return "\n".join(
        (node.text or "").strip()
        for node in root.iter()
        if _local_name(node.tag) == "t" and (node.text or "").strip()
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _summary(text: str) -> str:
    compact = " ".join((text or "").split())
    return compact[:500] + ("..." if len(compact) > 500 else "")
