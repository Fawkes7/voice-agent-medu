from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from httpx import HTTPStatusError

from app.api.auth import get_current_user
from app.config import get_settings
from app.models.user import User
from app.voice.elevenlabs import (
    SCRIBE_MODEL_ID,
    TTS_MODEL_ID,
    VoiceNotConfiguredError,
    create_scribe_token,
    stream_flash_tts,
)

router = APIRouter(prefix="/api/voice", tags=["voice"])


class TtsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    speaker: str = Field(default="patient")


@router.get("/config")
async def voice_config(_user: User = Depends(get_current_user)):
    settings = get_settings()
    return {
        "enabled": bool(settings.ELEVENLABS_API_KEY.strip()),
        "stt_model": SCRIBE_MODEL_ID,
        "tts_model": TTS_MODEL_ID,
        "language_code": settings.ELEVENLABS_STT_LANGUAGE,
    }


@router.post("/scribe-token")
async def scribe_token(_user: User = Depends(get_current_user)):
    """Mint a 15-minute single-use token for browser-side Scribe v2 Realtime."""
    try:
        token = await create_scribe_token()
    except VoiceNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs token 请求失败: {exc.response.status_code}",
        ) from exc
    return {
        "token": token,
        "model_id": SCRIBE_MODEL_ID,
        "expires_in_seconds": 15 * 60,
    }


@router.post("/tts")
async def text_to_speech(
    payload: TtsRequest,
    _user: User = Depends(get_current_user),
):
    """Synthesize agent speech with Eleven Flash v2.5. API key stays on the server."""
    speaker = payload.speaker if payload.speaker in ("patient", "tutor") else "patient"
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="text 不能为空")

    try:
        iterator = stream_flash_tts(text, speaker)
        first = await iterator.__anext__()
    except VoiceNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except StopAsyncIteration:
        raise HTTPException(status_code=502, detail="ElevenLabs TTS 返回空音频") from None
    except HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs TTS 失败: {exc.response.status_code}",
        ) from exc

    async def body():
        yield first
        async for chunk in iterator:
            yield chunk

    return StreamingResponse(
        body(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )
