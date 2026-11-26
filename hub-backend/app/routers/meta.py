from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from app.config import Settings
from app.deps import get_settings
from app.schemas import MetaCapabilities, MetaLimits, MetaResponse

router = APIRouter(prefix="/api/v1", tags=["meta"])


@router.get("/meta", response_model=MetaResponse)
def get_meta(
    response: Response,
    settings: Settings = Depends(get_settings),
) -> MetaResponse:
    response.headers["Cache-Control"] = "public, max-age=300"
    return MetaResponse(
        hub_id=settings.hub_id,
        hub_name=settings.hub_name,
        schema_version=settings.schema_version,
        capabilities=MetaCapabilities(
            video_file=settings.capability_video_file,
            feedback=settings.capability_feedback,
        ),
        limits=MetaLimits(
            max_image_bytes=settings.max_image_bytes,
            max_gif_bytes=settings.max_gif_bytes,
            max_images_per_submission=settings.max_images_per_submission,
            max_chat_nodes=settings.max_chat_nodes,
            max_chat_depth=settings.max_chat_depth,
        ),
        tags=settings.default_tags,
    )
