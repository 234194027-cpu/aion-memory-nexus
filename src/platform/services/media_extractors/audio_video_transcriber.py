from __future__ import annotations

from pathlib import Path
import json
import shutil
import subprocess
import wave

from src.platform.models.media_artifact import MediaArtifact
from src.platform.services.media_extractors.base import LocalExtractionInput
from src.shared.config import settings


class AudioVideoTranscriber:
    name = "audio_video_transcriber"
    version = "1"
    supported_types = {"audio", "video"}

    def can_extract(self, artifact: MediaArtifact, path: Path) -> bool:
        suffix = path.suffix.lower()
        return artifact.media_type in {"audio", "video"} or suffix in {".mp3", ".wav", ".m4a", ".mp4", ".mov"}

    async def extract(self, payload: LocalExtractionInput):
        from src.platform.services.media_ingestion import ExtractedNote

        metadata = _media_metadata(payload.path)
        try:
            text, duration = _run_faster_whisper(payload.path)
            warnings = [] if text else ["音视频没有转写出文本"]
            confidence = 0.62 if text else 0.25
        except RuntimeError as exc:
            text = ""
            warnings = [str(exc)]
            confidence = 0.25
        else:
            metadata = {**metadata, "duration_seconds": duration or metadata.get("duration_seconds")}
        title = payload.artifact.original_name or payload.path.name
        return ExtractedNote(
            title=f"音视频转写笔记：{title}"[:180],
            summary=_summary(text) if text else "音视频已接收，但当前环境未完成转写。",
            text=text[:20_000],
            structured_data={**metadata, "transcriber": "faster_whisper_optional"},
            source_url=payload.artifact.source_url or "",
            confidence=confidence,
            warnings=warnings,
        )


def _run_faster_whisper(path: Path) -> tuple[str, float | None]:
    if not settings.MEDIA_ENABLE_WHISPER:
        raise RuntimeError("whisper_disabled，音视频已入库，开启 MEDIA_ENABLE_WHISPER 后可转写")
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise RuntimeError("faster_whisper_not_installed，音视频已入库，等待转写依赖接入") from exc

    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(path), vad_filter=True)
    duration = getattr(info, "duration", None)
    if duration and duration > settings.MEDIA_MAX_TRANSCRIBE_SECONDS:
        raise RuntimeError("media_transcribe_duration_exceeded")
    text = "\n".join(segment.text.strip() for segment in segments if segment.text.strip())
    return text, duration


def _audio_metadata(path: Path) -> dict:
    if path.suffix.lower() != ".wav":
        return {}
    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            frame_count = audio.getnframes()
            return {
                "duration_seconds": round(frame_count / frame_rate, 4) if frame_rate else None,
                "channels": audio.getnchannels(),
                "sample_rate": frame_rate,
                "sample_width": audio.getsampwidth(),
                "metadata_reader": "wave",
            }
    except Exception:
        return {}


def _media_metadata(path: Path) -> dict:
    metadata = _ffprobe_metadata(path)
    if metadata:
        return metadata
    return _audio_metadata(path)


def _ffprobe_metadata(path: Path) -> dict:
    if not settings.MEDIA_ENABLE_FFMPEG:
        return {"media_probe": "ffmpeg_disabled"} if path.suffix.lower() in {".mp4", ".mov", ".m4a", ".mp3"} else {}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"media_probe": "ffprobe_not_found"}
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration,size:stream=codec_type,codec_name,width,height,sample_rate,channels",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        payload = json.loads(completed.stdout or "{}")
    except Exception as exc:
        return {"media_probe": "ffprobe_failed", "probe_error": str(exc)[:160]}
    format_info = payload.get("format") if isinstance(payload, dict) else {}
    streams = payload.get("streams") if isinstance(payload, dict) else []
    result: dict = {"media_probe": "ffprobe"}
    try:
        result["duration_seconds"] = round(float(format_info.get("duration")), 3)
    except Exception:
        pass
    try:
        result["size_bytes"] = int(format_info.get("size"))
    except Exception:
        pass
    if isinstance(streams, list):
        result["streams"] = [
            {
                key: stream.get(key)
                for key in ("codec_type", "codec_name", "width", "height", "sample_rate", "channels")
                if stream.get(key) is not None
            }
            for stream in streams[:8]
            if isinstance(stream, dict)
        ]
    return result


def _summary(text: str) -> str:
    compact = " ".join((text or "").split())
    return compact[:500] + ("..." if len(compact) > 500 else "")
