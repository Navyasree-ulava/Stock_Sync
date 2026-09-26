"""
routes/tts.py — ElevenLabs text-to-speech endpoint.
"""

import os
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth.dependencies import CurrentUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/tts", tags=["tts"])
limiter = Limiter(key_func=get_remote_address)

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "")
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")


class TTSRequest(BaseModel):
    text: str


@router.post("")
@limiter.limit("20/minute")
async def generate_tts(request: Request, body: TTSRequest, current_user: CurrentUser):
    """Generate speech for the authenticated user."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Text cannot be empty.")
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        raise HTTPException(
            status_code=503,
            detail="ElevenLabs credentials are not configured.",
        )

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY,
    }
    payload = {
        "text": text[:2000],
        "model_id": ELEVENLABS_MODEL,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        log.error("ElevenLabs TTS network error: %s", exc)
        raise HTTPException(status_code=502, detail="ElevenLabs text-to-speech is unavailable.")

    if response.status_code != 200:
        detail = response.text[:500]
        log.error("ElevenLabs TTS failed with status %s: %s", response.status_code, detail)
        raise HTTPException(
            status_code=502,
            detail="ElevenLabs text-to-speech request failed.",
        )

    return Response(content=response.content, media_type="audio/mpeg")
