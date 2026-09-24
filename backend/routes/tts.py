"""
routes/tts.py — ElevenLabs text-to-speech streaming endpoint.
"""

import os
import logging

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from auth.dependencies import CurrentUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/tts", tags=["tts"])

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "")


class TTSRequest(BaseModel):
    text: str


@router.post("")
async def generate_tts(request: TTSRequest, current_user: CurrentUser):
    """Stream audio from ElevenLabs. Requires authentication."""
    if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
        raise HTTPException(
            status_code=503,
            detail="ElevenLabs credentials not configured.",
        )

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": ELEVENLABS_API_KEY,
    }
    payload = {
        "text": request.text[:2000],  # Cap text length for safety
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
    }

    async def audio_stream():
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    log.error(f"[TTS] ElevenLabs error {resp.status_code}")
                    return
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(audio_stream(), media_type="audio/mpeg")
