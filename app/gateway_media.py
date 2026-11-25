"""Private media objects, scoped to the signed-in account and retained for 180 days."""

import hashlib
import os
import secrets
import time
from pathlib import Path

import boto3
from botocore.config import Config
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/api/gateway/media", tags=["Private media"])
FORMATS = {
    "image/png": ("image", "png", 3_500_000),
    "image/jpeg": ("image", "jpeg", 3_500_000),
    "image/webp": ("image", "webp", 3_500_000),
    "audio/wav": ("audio", "wav", 10_000_000),
    "audio/x-wav": ("audio", "wav", 10_000_000),
    "audio/mpeg": ("audio", "mp3", 10_000_000),
    "video/mp4": ("video", "mp4", 20_000_000),
    "video/webm": ("video", "webm", 20_000_000),
}


def detect(raw, mime):
    valid = {
        "png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpeg": raw.startswith(b"\xff\xd8\xff"),
        "webp": raw[:4] == b"RIFF" and raw[8:12] == b"WEBP",
        "wav": raw[:4] == b"RIFF" and raw[8:12] == b"WAVE",
        "mp3": raw[:3] == b"ID3" or (len(raw) > 2 and raw[0] == 255 and raw[1] & 224 == 224),
        "mp4": raw[4:8] == b"ftyp",
        "webm": raw[:4] == b"\x1aE\xdf\xa3",
    }
    if mime not in FORMATS or not valid[FORMATS[mime][1]]:
        raise HTTPException(422, "文件内容与支持的图片、音频或视频格式不符。")


class MediaStore:
    def __init__(self, records):
        self.records = records
        self.bucket = os.getenv("GATEWAY_MEDIA_BUCKET")
        self.local = Path(os.getenv("GATEWAY_MEDIA_DIR", ".data/media"))
        self.s3 = boto3.client(
            "s3", region_name=os.getenv("AWS_REGION", "us-east-1"),
            config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 1}),
        ) if self.bucket else None

    @staticmethod
    def key(user, asset_id):
        # Never accept an object key or URI supplied by the caller.
        owner = hashlib.sha256(user.encode()).hexdigest()
        return f"media/{owner}/{asset_id}"

    def put(self, user, raw, mime, *, generated=False):
        if self.records.table and not self.bucket:
            raise HTTPException(503, "生产服务尚未配置专用媒体存储。")
        if mime not in FORMATS or not raw or len(raw) > (25_000_000 if generated else FORMATS[mime][2]):
            raise HTTPException(413, "文件为空或超过当前上传限制。")
        detect(raw, mime)
        asset_id = secrets.token_hex(16)
        key = self.key(user, asset_id)
        if self.s3:
            self.s3.put_object(
                Bucket=self.bucket, Key=key, Body=raw, ContentType=mime,
                ServerSideEncryption="AES256", CacheControl="private, no-store",
            )
        else:
            target = self.local / key
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(raw)
            target.chmod(0o600)
        item = {
            "id": asset_id, "kind": FORMATS[mime][0], "format": FORMATS[mime][1],
            "content_type": mime, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "created_at": int(time.time()), "expires_at": int(time.time()) + 180 * 86400,
            "generated": generated, "url": f"/api/gateway/media/{asset_id}",
        }
        self.records.put("USER#" + user, "MEDIA#" + asset_id, item)
        return item

    def metadata(self, user, asset_id):
        item = self.records.get("USER#" + user, "MEDIA#" + asset_id)
        if not item or item["expires_at"] <= time.time():
            raise HTTPException(404, "媒体文件不存在或已过期。")
        return item

    def resolve(self, user, refs):
        items = []
        for ref in refs:
            item = self.metadata(user, ref.id)
            if item["kind"] != ref.kind:
                raise HTTPException(422, "附件类型与已上传文件不一致。")
            items.append(item)
        return items

    def read(self, user, asset_id):
        item = self.metadata(user, asset_id)
        key = self.key(user, asset_id)
        if self.s3:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            try:
                raw = response["Body"].read(25_000_001)
            finally:
                response["Body"].close()
        else:
            raw = (self.local / key).read_bytes()
        if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise HTTPException(502, "媒体内容校验失败。")
        return item, raw


@router.post("")
async def upload(request: Request):
    mime = request.headers.get("content-type", "").split(";")[0].lower()
    if mime not in FORMATS:
        raise HTTPException(415, "支持 PNG/JPEG/WebP、WAV/MP3、MP4/WebM。")
    limit = FORMATS[mime][2]
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > limit:
            raise HTTPException(413, f"此格式最多上传 {limit // 1_000_000} MB。")
    return await run_in_threadpool(
        request.app.state.media.put, request.state.user["username"], bytes(content), mime,
    )


@router.get("/{asset_id}")
async def download(asset_id: str, request: Request):
    item, raw = await run_in_threadpool(
        request.app.state.media.read, request.state.user["username"], asset_id,
    )
    return Response(
        raw, media_type=item["content_type"],
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, request: Request):
    media, user = request.app.state.media, request.state.user["username"]
    job = await run_in_threadpool(media.records.get, "USER#" + user, "MEDIAJOB#" + job_id)
    if not job:
        raise HTTPException(404, "生成任务不存在。")
    if job["status"] == "running":
        client = request.app.state.bedrock_target
        result = await run_in_threadpool(client.get_async_invoke, invocationArn=job["invocation_arn"])
        if result["status"] == "Completed":
            objects = await run_in_threadpool(
                media.s3.list_objects_v2, Bucket=media.bucket, Prefix=job["prefix"],
            )
            for obj in objects.get("Contents", []):
                if obj["Key"].endswith(".mp4") and obj["Size"] <= 25_000_000:
                    content = await run_in_threadpool(
                        media.s3.get_object, Bucket=media.bucket, Key=obj["Key"],
                    )
                    try:
                        raw = await run_in_threadpool(content["Body"].read, 25_000_001)
                    finally:
                        content["Body"].close()
                    artifact = await run_in_threadpool(
                        media.put, user, raw, "video/mp4", generated=True,
                    )
                    job["artifacts"].append(artifact)
            job["status"] = "completed" if job["artifacts"] else "failed"
        elif result["status"] == "Failed":
            job["status"] = "failed"
            job["error"] = "Bedrock 视频生成失败。"
        await run_in_threadpool(media.records.put, "USER#" + user, "MEDIAJOB#" + job_id, job)
    return {k: job.get(k) for k in ("id", "status", "url", "artifacts", "error")}
