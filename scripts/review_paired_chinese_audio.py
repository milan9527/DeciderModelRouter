"""Calibrated Chinese audio review and English cross-check with Polly and Transcribe."""

import argparse
import io
import json
import re
import time
import uuid
import wave
from pathlib import Path

import boto3

from scripts.run_paired_quality import ARMS
from scripts.run_profile_quality import read, save


def normalized(text):
    return "".join(re.findall(r"\w+", text.casefold()))


def cleanup_prefix(s3, bucket, prefix):
    """Remove only this run's objects, versions, markers and Transcribe write probes."""
    if not re.fullmatch(r"temporary-evaluation/paired-audio-[0-9a-f]{32}/", prefix):
        raise ValueError("Refusing cleanup outside an exact generated evaluation prefix")
    objects = []
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
            objects.append({"Key": item["Key"], "VersionId": item["VersionId"]})
    errors = []
    for start in range(0, len(objects), 1000):
        response = s3.delete_objects(Bucket=bucket, Delete={"Objects": objects[start:start + 1000]})
        errors.extend(response.get("Errors", []))
    return {"versions_and_markers_deleted": len(objects), "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--bucket", required=True, help="Existing private project media bucket")
    parser.add_argument("--arms", choices=ARMS, nargs="+", default=list(ARMS))
    parser.add_argument("--output-name", default="chinese-audio-check")
    parser.add_argument("--target-case", choices=["tts-zh", "tts"], default="tts-zh")
    args = parser.parse_args()
    sources = {arm: read(args.input / arm / "base" / (args.target_case + ".json.gz")) for arm in args.arms}
    if any(row["status"] == "started" for row in sources.values()):
        raise ValueError("Wait for all requested target attempts to finish before grading")
    root = args.input / args.output_name
    root.mkdir(exist_ok=True)
    report_path = root / "results.json"
    if report_path.exists():
        raise ValueError("Existing review is immutable; inspect it rather than silently retrying")
    tag = "paired-audio-" + uuid.uuid4().hex
    prefix = "temporary-evaluation/" + tag + "/"
    s3 = boto3.client("s3", region_name="us-east-1")
    transcribe = boto3.client("transcribe", region_name="us-east-1")
    polly = boto3.client("polly", region_name="us-east-1")
    created = []
    expected = "欢迎使用模型网关。" if args.target_case == "tts-zh" else "Hello, welcome to the model gateway."
    controls = [
        ("control-expected", expected, "Zhiyu" if args.target_case == "tts-zh" else "Joanna"),
        ("control-different", "请关闭模型网关。" if args.target_case == "tts-zh" else
         "Please close the model gateway.", "Zhiyu" if args.target_case == "tts-zh" else "Joanna"),
        ("control-english", "Good morning. The package has not arrived.", "Joanna"),
    ]
    if args.target_case == "tts":
        controls.append(("control-spanish", "Hola, buenos días.", "Lupe"))
    languages = ["zh-CN", "en-US"] if args.target_case == "tts-zh" else ["en-US", "es-US"]
    report = {"method": "aws_transcribe_auto_language_with_polly_controls", "target_case": args.target_case,
              "language_options": languages, "targets": {},
              "controls": {}, "scope": "spoken content only; no human listening or voice-quality assessment"}
    save(report_path, {**report, "status": "started"})
    try:
        inputs = {}
        for name, text, voice in controls:
            response = polly.synthesize_speech(Text=text, VoiceId=voice, Engine="standard",
                                               OutputFormat="pcm", SampleRate="16000")
            pcm = response["AudioStream"].read()
            response["AudioStream"].close()
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(pcm)
            inputs[name] = buffer.getvalue()
            (root / (name + ".wav")).write_bytes(inputs[name])
            report["controls"][name] = {"expected": text, "polly_voice": voice}
        for arm, row in sources.items():
            if row["status"] == "completed":
                path = args.input / arm / row["output"]["artifacts"][0]["local_path"]
                inputs[arm] = path.read_bytes()
                report["targets"][arm] = {"expected": expected,
                                           "artifact": str(path.relative_to(args.input))}
        jobs = {}
        for name, raw in inputs.items():
            key, output_key = prefix + name + ".wav", prefix + name + ".json"
            s3.put_object(Bucket=args.bucket, Key=key, Body=raw, ContentType="audio/wav",
                          ServerSideEncryption="AES256")
            job = tag + "-" + name.replace("_", "-")
            transcribe.start_transcription_job(
                TranscriptionJobName=job, Media={"MediaFileUri": f"s3://{args.bucket}/{key}"},
                MediaFormat="wav", IdentifyLanguage=True, LanguageOptions=languages,
                OutputBucketName=args.bucket, OutputKey=output_key)
            created.append(job)
            jobs[name] = (job, output_key)
        deadline = time.monotonic() + 300
        while jobs and time.monotonic() < deadline:
            for name, (job, key) in list(jobs.items()):
                value = transcribe.get_transcription_job(TranscriptionJobName=job)["TranscriptionJob"]
                status = value["TranscriptionJobStatus"]
                if status not in {"COMPLETED", "FAILED"}:
                    continue
                group = report["controls"] if name.startswith("control-") else report["targets"]
                group[name].update(job_status=status, language=value.get("LanguageCode"),
                                   language_score=value.get("IdentifiedLanguageScore"))
                if status == "COMPLETED":
                    response = s3.get_object(Bucket=args.bucket, Key=key)
                    data = json.loads(response["Body"].read())
                    response["Body"].close()
                    text = " ".join(r["transcript"] for r in data["results"]["transcripts"])
                    group[name].update(transcript=text, raw_transcription=data,
                                       exact_content_match=normalized(text) == normalized(group[name]["expected"]))
                else:
                    group[name]["error"] = value.get("FailureReason")
                del jobs[name]
                save(report_path, {**report, "status": "running"})
                print(name, status, group[name].get("transcript"), flush=True)
            if jobs:
                time.sleep(5)
        for name in jobs:
            group = report["controls"] if name.startswith("control-") else report["targets"]
            group[name]["error"] = "transcription review timed out"
        calibrated = len(report["controls"]) == len(controls) and all(
            item.get("exact_content_match") for item in report["controls"].values())
        report.update(status="completed", calibration_pass=calibrated)
        for value in report["targets"].values():
            value["status"] = ("pass" if value.get("exact_content_match") else "fail") if (
                calibrated and value.get("job_status") == "COMPLETED") else "pending"
    except Exception as exc:
        report.update(status="error", error=str(exc))
    finally:
        cleanup_errors = []
        for job in created:
            try:
                transcribe.delete_transcription_job(TranscriptionJobName=job)
            except Exception as exc:
                cleanup_errors.append({"job": job, "error": str(exc)})
        try:
            report["s3_cleanup"] = cleanup_prefix(s3, args.bucket, prefix)
            cleanup_errors.extend(report["s3_cleanup"]["errors"])
        except Exception as exc:
            cleanup_errors.append({"s3_prefix": prefix, "error": str(exc)})
        report["cleanup_errors"] = cleanup_errors
        report["temporary_s3_prefix"] = prefix
        save(report_path, report)
        for client in (s3, transcribe, polly):
            client.close()
    print(json.dumps({"status": report["status"], "calibration_pass": report.get("calibration_pass"),
                      "targets": {a: v.get("status") for a, v in report["targets"].items()},
                      "cleanup_errors": len(report["cleanup_errors"])}))


if __name__ == "__main__":
    main()
