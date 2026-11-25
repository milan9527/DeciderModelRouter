"""References to authenticated media; file bytes never enter routing/history JSON."""

from typing import Annotated, Literal

from pydantic import Field

from app.schemas import StrictModel


class MediaRef(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    kind: Literal["image", "audio", "video"]


OutputModality = Literal["auto", "text", "image", "audio", "video", "embedding", "rerank"]
ImageOperation = Literal[
    "generate", "remove_background", "upscale", "creative_upscale",
    "conservative_upscale", "sketch", "structure", "inpaint",
]


class MediaOptions(StrictModel):
    image_operation: ImageOperation = "generate"
    voice: Literal["matthew", "tiffany", "amy", "lupe", "carlos"] = "tiffany"
    seed: int = Field(default=0, ge=0, le=4294967294)
    documents: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        default_factory=list, max_length=20,
    )
