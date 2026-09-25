from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import current_user
from app.persistence.models import Attachment
from app.persistence.session import get_session
from app.registry.service import error
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/attachments")


def require_image_input(settings: Settings, attachment_ids: list[str]) -> None:
    if attachment_ids and "supported" not in (settings.llm_image_input, settings.audio_input):
        raise error(422, "image_input_unsupported", "Image input is not verified for this provider.")


def require_media_input(settings: Settings, attachments: list[Attachment]) -> None:
    for attachment in attachments:
        if attachment.mime.startswith("audio/") and settings.audio_input != "supported":
            raise error(422, "audio_input_unsupported", "Audio input is not verified for this provider.")
        if attachment.mime.startswith("image/") and settings.llm_image_input != "supported":
            raise error(422, "image_input_unsupported", "Image input is not verified for this provider.")


def sniff_media(data: bytes) -> str | None:
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE" and len(data) >= 44:
        return "audio/wav"
    return sniff_image(data)


def sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 16:
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 14:
        return "image/gif"
    return None


@router.post("", status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    user_sub: str = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    data = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    if len(data) > settings.max_upload_bytes:
        raise error(413, "file_too_large", "File exceeds the upload limit.")
    detected = sniff_media(data)
    allowed = settings.allowed_image_mime.split(",") + settings.allowed_audio_mime.split(",")
    if not detected or detected != file.content_type or detected not in allowed:
        raise error(415, "unsupported_media_type", "File format does not match an allowed MIME type.")
    attachment = Attachment(user_sub=user_sub, mime=detected, size=len(data), data=data)
    session.add(attachment)
    await session.commit()
    return {"id": attachment.id, "mime": attachment.mime, "size": attachment.size}


@router.get("/{attachment_id}")
async def get_attachment(attachment_id: str, user_sub: str = Depends(current_user), session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Attachment).where(Attachment.id == attachment_id, Attachment.user_sub == user_sub))
    attachment = result.scalar_one_or_none()
    if attachment is None:
        raise error(404, "attachment_not_found", "Attachment not found.")
    return Response(attachment.data, media_type=attachment.mime, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})