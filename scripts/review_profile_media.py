"""Inspect actual generated artifacts; do not infer media quality from API success."""

import argparse
import base64
import hashlib
import io
import re
import wave
from pathlib import Path

import boto3
from botocore.config import Config
from PIL import Image

from app.media_execution import invoke_json
from scripts.run_profile_quality import read, save


def normalized(text):
    return "".join(re.findall(r"\w+", text.casefold()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    client = boto3.client("bedrock-runtime", region_name="us-east-1",
                          config=Config(connect_timeout=10, read_timeout=120,
                                        retries={"total_max_attempts": 1}))
    for cid in ("tts", "tts-zh", "image-edit"):
        source = args.input / "base" / (cid + ".json.gz")
        output = args.input / "media-reviews" / (cid + ".json")
        if not source.exists() or output.exists():
            continue
        row = read(source)
        if row["status"] != "completed":
            continue
        artifacts = row.get("output", {}).get("artifacts", [])
        review = {"case_id": cid, "status": "pending", "method": "actual_artifact_inspection"}
        save(output, review)
        try:
            if not artifacts:
                raise ValueError("No generated artifact")
            raw = (args.input / artifacts[0]["local_path"]).read_bytes()
            review["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
            if cid.startswith("tts"):
                with wave.open(io.BytesIO(raw)) as recording:
                    review["audio"] = {"rate": recording.getframerate(), "channels": recording.getnchannels(),
                                       "seconds": recording.getnframes() / recording.getframerate(),
                                       "sample_width": recording.getsampwidth()}
                response = invoke_json(client, "mistral.voxtral-mini-3b-2507", {
                    "messages": [{"role": "user", "content": [
                        {"type": "input_audio", "input_audio": {"data": base64.b64encode(raw).decode(), "format": "wav"}},
                        {"type": "text", "text": "Transcribe the spoken words only, in the original language."},
                    ]}], "max_tokens": 1024,
                })
                choice = response["choices"][0]
                text = choice["message"]["content"]
                expected = "Hello, welcome to the model gateway." if cid == "tts" else "欢迎使用模型网关。"
                complete = choice.get("finish_reason", choice.get("stop_reason")) in ("stop", "end_turn")
                review.update(status="pass" if complete and normalized(text) == normalized(expected) else "fail",
                              transcript=text, expected=expected, asr_response=response,
                              judge_model="mistral.voxtral-mini-3b-2507",
                              reason="实际WAV可解码，独立ASR检查朗读内容；不评价音色和自然度。")
            else:
                original = Image.open(Path("app/evaluation_assets/chart.png")).convert("RGBA")
                edited = Image.open(io.BytesIO(raw)).convert("RGBA")
                review.update(original_size=list(original.size), output_size=list(edited.size))
                if edited.size != original.size:
                    review.update(status="fail", reason="Background removal changed image dimensions")
                else:
                    before, after = list(original.getdata()), list(edited.getdata())
                    colored = [i for i, p in enumerate(before) if max(p[:3])-min(p[:3]) > 50 and max(p[:3]) > 100]
                    white = [i for i, p in enumerate(before) if min(p[:3]) > 245]
                    review.update(
                        colored_pixels=len(colored), white_pixels=len(white),
                        colored_retained=sum(after[i][3] > 200 for i in colored) / max(1, len(colored)),
                        white_removed=sum(after[i][3] < 30 for i in white) / max(1, len(white)),
                        status="pending", reason="Pixel measurements recorded; visual review of plot, labels and axes required")
        except Exception as exc:
            review.update(status="pending", error=str(exc))
        save(output, review)
        print(cid, review["status"], flush=True)
    client.close()


if __name__ == "__main__":
    main()
