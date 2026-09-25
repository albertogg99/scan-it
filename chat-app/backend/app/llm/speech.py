"""Speech-to-text through an OpenAI-compatible /audio/transcriptions endpoint."""

from openai import AsyncOpenAI, OpenAIError

from app.settings import Settings


class TranscriptionError(Exception):
    pass


async def transcribe(settings: Settings, data: bytes, mime: str) -> str:
    client = AsyncOpenAI(api_key=settings.stt_api_key or "unused", base_url=settings.stt_base_url,
                         timeout=settings.stt_timeout_s, max_retries=1)
    try:
        result = await client.audio.transcriptions.create(model=settings.stt_model, file=("voice.wav", data, mime))
    except OpenAIError as exc:
        raise TranscriptionError(type(exc).__name__) from exc
    finally:
        await client.close()
    return result.text.strip()
