"""Bounded real GPU/Bedrock acceptance checks; requires private login credentials."""

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

from app.gateway_store import clean
from app.routing import Task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    base = args.url.rstrip("/")
    report = {"url": base, "fixture": False, "cases": [], "uploads": {}}

    def save(name, value):
        (args.output / name).write_text(
            json.dumps(clean(value), ensure_ascii=False, indent=2) + "\n"
        )

    with httpx.Client(
        base_url=base, headers={"X-Decider-Client": "web", "Origin": base},
        timeout=180, trust_env=False,
    ) as client:
        root = client.get("/")
        assert root.status_code == 200 and "location" not in root.headers
        assert client.get("/api/gateway/catalog").status_code == 401
        client.post("/api/auth/login", json=json.loads(args.credentials.read_text())).raise_for_status()
        catalog = client.get("/api/gateway/catalog").json()
        assert catalog["policy_version"] == "bedrock-classified-v3"
        save("capabilities.json", catalog["media"])
        report.update(
            root_status=root.status_code, template_count=len(catalog["examples"]),
            policy_version=catalog["policy_version"],
        )
        session = client.post(
            "/api/gateway/sessions", json={"title": "通用与多模态上线验收"},
        ).json()
        refs = {}
        for kind, path, mime in (
            ("image", args.image, "image/png"), ("audio", args.audio, "audio/wav"),
            ("video", args.video, "video/mp4"),
        ):
            raw = path.read_bytes()
            response = client.post("/api/gateway/media", content=raw, headers={"Content-Type": mime})
            response.raise_for_status()
            asset = response.json()
            downloaded = client.get(asset["url"])
            assert downloaded.content == raw and "no-store" in downloaded.headers["cache-control"]
            refs[kind] = [{"id": asset["id"], "kind": kind}]
            report["uploads"][kind] = {
                "id": asset["id"], "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            }
        cases = [
            ("hi", {"text": "hi"}, "general_text", True),
            ("everyday", {"text": "为什么天空是蓝色的？请用一句话回答。"}, "general_text", True),
            ("image", {"text": "Describe this image.", "attachments": refs["image"], "output_modality": "text"}, "vision", True),
            ("video", {"text": "Describe this video briefly.", "attachments": refs["video"], "output_modality": "text"}, "video_understanding", True),
            ("audio", {"text": "Transcribe the spoken words.", "attachments": refs["audio"], "output_modality": "text"}, "audio_understanding", True),
            ("speech", {"text": "Read aloud: Hello, welcome to the model gateway.", "output_modality": "audio"}, "speech", True),
            ("image-edit", {"text": "Remove the background.", "attachments": refs["image"], "output_modality": "image", "media_options": {"image_operation": "remove_background"}}, "image", True),
            ("rerank", {"text": "Which animal barks?", "output_modality": "rerank", "media_options": {"documents": ["Dogs bark.", "Cats meow.", "Birds sing."]}}, "rerank", True),
            ("unavailable-video", {"text": "Generate a video of waves.", "output_modality": "video"}, "video", False),
            ("unavailable-image", {"text": "Generate an image of a flower.", "output_modality": "image"}, "image", False),
        ]
        for name, task, workflow, executable in cases:
            task["routing_preferences"] = {"expected_output_tokens": 128, "max_output_tokens": 512}
            payload = {
                "task": task, "provider": "h2o", "mode": "live", "execute_target": executable,
                "session_id": session["id"], "client_request_id": str(uuid4()),
                **{k: catalog[k] for k in ("catalog_version", "ranking_version", "policy_version")},
            }
            started = perf_counter()
            with client.stream("POST", "/api/gateway/runs", json=payload) as response:
                response.raise_for_status()
                events = [json.loads(line) for line in response.iter_lines() if line.strip()]
            save(name + ".json", {"request": payload, "events": events})
            route = next((e["data"] for e in events if e["type"] == "route"), {})
            profile = route.get("steps", [{}])[0]
            output = next((e["data"] for e in events if e.get("stage") == "execution" and e.get("status") == "completed"), {})
            row = {
                "case": name, "request_id": events[0]["request_id"],
                "expected_workflow": workflow, "workflow": profile.get("choice"),
                "status": events[-1]["data"].get("status"),
                "model": output.get("model_id"), "usage": output.get("usage"),
                "route_ms": route.get("route_total_ms"), "target_ms": output.get("backend_total_ms"),
                "client_ms": round((perf_counter() - started) * 1000, 3),
                "target_started": any(e.get("stage") == "execution" and e.get("status") == "started" for e in events),
                "artifacts": [], "error": events[-1]["data"] if events[-1]["type"] == "error" else None,
            }
            for asset in output.get("artifacts", []):
                downloaded = client.get(asset["url"])
                downloaded.raise_for_status()
                assert hashlib.sha256(downloaded.content).hexdigest() == asset["sha256"]
                row["artifacts"].append({k: asset[k] for k in ("id", "kind", "size", "sha256")})
            saved = client.get("/api/gateway/history/" + row["request_id"])
            saved.raise_for_status()
            row["history_persisted"] = saved.json()["request"]["task"] == Task(**task).model_dump()
            row["passed"] = (
                row["workflow"] == workflow and row["history_persisted"]
                and row["status"] == ("executed" if executable else "no_route")
            )
            report["cases"].append(row)
            save("verification.json", report)
            print(json.dumps(row, ensure_ascii=False), flush=True)
        client.post("/api/auth/logout").raise_for_status()
        for value in report["uploads"].values():
            assert client.get("/api/gateway/media/" + value["id"]).status_code == 401
        report.update(
            anonymous_media_status=401,
            gpu_calls=len(cases),
            target_attempts=sum(r["target_started"] for r in report["cases"]),
            passed=all(r["passed"] for r in report["cases"]),
        )
        save("verification.json", report)
        assert report["passed"], "Inspect saved cases; failed requests are never retried automatically."


if __name__ == "__main__":
    main()
