from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from app.config import get_settings

ELEVENLABS_API_BASE = "https://api.elevenlabs.io"
SCRIBE_MODEL_ID = "scribe_v2_realtime"
TTS_MODEL_ID = "eleven_flash_v2_5"


class VoiceNotConfiguredError(RuntimeError):
    pass


def require_api_key() -> str:
    key = get_settings().ELEVENLABS_API_KEY.strip()
    if not key:
        raise VoiceNotConfiguredError("未配置 ELEVENLABS_API_KEY，无法启用语音问诊")
    return key


async def create_scribe_token() -> str:
    api_key = require_api_key()
    url = f"{ELEVENLABS_API_BASE}/v1/single-use-token/realtime_scribe"
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, headers={"xi-api-key": api_key})
        response.raise_for_status()
        payload = response.json()
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("ElevenLabs 未返回有效的 Scribe token")
    return token


def voice_id_for_speaker(speaker: str) -> str:
    settings = get_settings()
    if speaker == "tutor":
        return settings.ELEVENLABS_VOICE_ID_TUTOR
    return settings.ELEVENLABS_VOICE_ID_PATIENT


async def stream_flash_tts(text: str, speaker: str = "patient") -> AsyncIterator[bytes]:
    api_key = require_api_key()
    voice_id = voice_id_for_speaker(speaker)
    url = f"{ELEVENLABS_API_BASE}/v1/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    settings = get_settings()
    body = {
        "text": text,
        "model_id": settings.ELEVENLABS_TTS_MODEL or TTS_MODEL_ID,
        # Force Mandarin pronunciation for Chinese medical students.
        "language_code": settings.ELEVENLABS_STT_LANGUAGE or "zh",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.75,
        },
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=body,
            params={"optimize_streaming_latency": 3},
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk
