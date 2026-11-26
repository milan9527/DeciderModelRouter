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
    "erase", "outpaint", "recolor", "replace", "style_guide", "style_transfer",
]


class MediaOptions(StrictModel):
    image_operation: ImageOperation = "generate"
    voice: Literal["matthew", "tiffany", "amy", "lupe", "carlos"] = "tiffany"
    seed: int = Field(default=0, ge=0, le=4294967294)
    edit_target: str = Field(default="", max_length=1000)
    outpaint_pixels: int = Field(default=128, ge=1, le=1024)
    documents: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        default_factory=list, max_length=20,
    )
