"""Reproducible narrow checks on generated artifacts, separate from interface success."""

import argparse
import hashlib
import json
from pathlib import Path

import boto3
from botocore.config import Config
from PIL import Image, ImageStat

from app.gateway_effect_judge import grade
from app.media_execution import native
from app.routing import Task


def image_check(path, model_id, compatible_prompt=False):
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    rgb = im.convert("RGB")
    center = ImageStat.Stat(rgb.crop((w*3//8, h*3//8, w*5//8, h*5//8))).mean
    corner = ImageStat.Stat(rgb.crop((0, 0, w//8, h//8))).mean
    red = center[0] > center[1] + 50 and center[0] > center[2] + 50
    blue = center[2] > center[0] + 20 and center[2] > center[1] + 15
    white = min(corner) >= 230
    if "remove-background" in model_id:
        passed = im.getpixel((0, 0))[3] < 10 and im.getpixel((w//2, h//2))[3] > 240 and red
        criterion = "透明背景、中心红色前景保留"
    elif "fast-upscale" in model_id:
        passed, criterion = w == h == 2048 and red and white, "4倍尺寸、红色中心与白色背景保留"
    elif "erase-object" in model_id:
        passed, criterion = min(center) >= 230 and white, "遮罩内红色对象消失、白色背景保持"
    elif "conservative-upscale" in model_id or "outpaint" in model_id:
        if not compatible_prompt:
            return {"status": "pending", "source": "fixture_limitation",
                    "reason": "本次蓝色提示与保留红色原图的操作要求存在歧义，仅记录接口成功；不据此判断质量"}
        if "outpaint" in model_id:
            passed, criterion = w == 640 and h == 512 and red and white, "左扩128像素、红色对象与白色背景保留"
        else:
            passed, criterion = w > 512 and h > 512 and red and white, "尺寸增大、红色中心与白色背景保留"
    else:
        passed, criterion = blue and white, "中心区域变蓝、外侧背景保持白色；仅检查颜色，不评价审美"
    return {"status": "pass" if passed else "fail", "source": "artifact_pixel_check",
            "reason": criterion, "center_rgb": center, "corner_rgb": corner, "size": [w, h]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurements", type=Path, required=True)
    args = parser.parse_args()
    folder = args.measurements / "artifact-reviews"
    folder.mkdir(exist_ok=True)
    client = boto3.client("bedrock-runtime", region_name="us-east-1",
                          config=Config(connect_timeout=5, read_timeout=90, retries={"max_attempts": 0}))
    for path in sorted((args.measurements / "observations").glob("*.json")):
        row = json.loads(path.read_text())
        if row["status"] != "completed" or row["workflow"] not in {"image", "speech"}:
            continue
        output = row["output"]
        artifact = args.measurements / output["artifact"]
        raw = artifact.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if sha != output["sha256"]:
            raise ValueError("Artifact changed")
        target = folder / path.name
        if target.exists():
            continue
        if row["workflow"] == "image":
            review = image_check(artifact, row["model_id"], row["case_id"] == "image-edit-v2")
        else:
            asr_model = "mistral.voxtral-mini-3b-2507"
            result = native(client, {"adapter": "voxtral_audio", "model": {"invoke_id": asr_model}},
                            Task(text="Transcribe the spoken words exactly. Return only the transcript."),
                            [({"format": "wav"}, raw)])
            review = {**grade({"judge": {"kind": "transcript",
                "expected": "Hello, welcome to the model gateway."}}, result),
                "source": "independent_asr_check", "asr_model": asr_model, "asr_output": result,
                "reason": "独立ASR回译匹配测试句；不评估音色自然度或其他语言"}
        target.write_text(json.dumps({**review, "artifact_sha256": sha,
            "output_sha256": hashlib.sha256(json.dumps(output, sort_keys=True).encode()).hexdigest()},
            ensure_ascii=False, indent=2) + "\n")
        print(row["model_id"], review["status"], flush=True)


if __name__ == "__main__":
    main()
