from fastapi import APIRouter, Depends

from app.settings import Settings, get_settings

router = APIRouter()


@router.get("/api/config/public")
async def public_config(settings: Settings = Depends(get_settings)):
    return {"app_name": settings.app_name, "llm_configured": bool(settings.api_key), "image_input": settings.llm_image_input,
            "audio_input": settings.audio_input}