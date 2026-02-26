"""Voice message processing - speech-to-text using local faster-whisper."""

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Singleton model - loaded once, reused
_whisper_model = None


def _get_model():
    """Get or load the whisper model (lazy singleton)."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        logger.info("Loading faster-whisper tiny model...")
        _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
        logger.info("Whisper model ready")
    return _whisper_model


def _convert_ogg_to_wav(ogg_path: str) -> str:
    """Convert Telegram OGG/OPUS to WAV for whisper."""
    wav_path = ogg_path.rsplit(".", 1)[0] + ".wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
        capture_output=True,
    )
    return wav_path


def _transcribe_sync(file_path: str) -> str:
    """Run transcription in a thread (blocking)."""
    try:
        # Convert to WAV first
        wav_path = _convert_ogg_to_wav(file_path)
        target = wav_path if Path(wav_path).exists() else file_path

        model = _get_model()
        segments, info = model.transcribe(target, beam_size=3)
        text = " ".join(seg.text for seg in segments).strip()

        # Clean up wav
        Path(wav_path).unlink(missing_ok=True)

        logger.info(f"Transcribed ({info.language}, {info.duration:.1f}s): {text[:100]}")
        return text

    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return ""


async def transcribe_voice(file_path: str) -> str:
    """Transcribe a voice message file. Runs in thread pool to avoid blocking."""
    return await asyncio.to_thread(_transcribe_sync, file_path)


async def preload_model():
    """Pre-load whisper model at startup so first request is fast."""
    await asyncio.to_thread(_get_model)
