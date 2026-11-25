"""Run or resume a bounded authenticated effectiveness job; never retry POST creation."""

import argparse
import json
import time
from pathlib import Path

import httpx

from app.gateway_store import clean


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--credentials", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--providers", nargs="+", default=["decider", "liquid", "h2o"])
    p.add_argument("--cases", nargs="+", help="Omit to run the entire extensible catalog")
    p.add_argument("--baselines", action="store_true")
    p.add_argument("--max-target-calls", type=int, default=192)
    p.add_argument("--max-output-tokens", type=int, default=16384)
    p.add_argument("--resume", help="Existing job ID; performs no new model calls")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=bool(args.resume))
    base = args.url.rstrip("/")

    def save(name, data):
        path = args.output / name
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(clean(data), indent=2, ensure_ascii=False) + "\n")
        temporary.replace(path)

    with httpx.Client(base_url=base, headers={"X-Decider-Client": "web", "Origin": base},
                      timeout=90, trust_env=False) as client:
        response = client.post("/api/auth/login", json=json.loads(args.credentials.read_text()))
        response.raise_for_status()
        cases = client.get("/api/gateway/effects/cases")
        cases.raise_for_status()
        save("current-suite.json" if args.resume and (args.output / "suite.json").exists()
             else "suite.json", cases.json())
        if args.resume:
            jid = args.resume
        else:
            payload = {
                "providers": args.providers,
                "case_ids": args.cases or [c["id"] for c in cases.json()["items"]],
                "baselines": args.baselines, "max_target_calls": args.max_target_calls,
                "max_output_tokens": args.max_output_tokens,
            }
            save("request.json", payload)
            response = client.post("/api/gateway/effects", json=payload)
            response.raise_for_status()
            jid = response.json()["id"]
            save("job.json", response.json())
        last = None
        deadline = time.monotonic() + 7200
        while time.monotonic() < deadline:
            response = client.get("/api/gateway/effects/" + jid)
            response.raise_for_status()
            job = response.json()
            save("evaluation.json", job)
            progress = {k: job[k] for k in ("id", "status", "started_gpu_calls", "target_model_calls", "recorded_rows")}
            if progress != last:
                print(json.dumps(progress), flush=True)
                last = progress
            if job["status"] != "running":
                if len(job["results"]) != job["recorded_rows"]:
                    raise SystemExit("Export is missing persisted rows; resume after fixing the read path.")
                save("summary.json", job["summary"])
                if job["status"] != "completed":
                    raise SystemExit("Job did not complete; retained results are exported.")
                return
            time.sleep(5)
        raise SystemExit(f"Polling time limit reached. Job was not cancelled. Resume with --resume {jid}")


if __name__ == "__main__":
    main()
