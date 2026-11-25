"""Submit explicit, evidence-backed reviews; never grade or invoke models automatically."""

import argparse
import json
from pathlib import Path

import httpx


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--credentials", required=True, type=Path)
    p.add_argument("--job-id", required=True)
    p.add_argument("--reviews", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    base = args.url.rstrip("/")
    planned = json.loads(args.reviews.read_text())
    audit = []
    with httpx.Client(base_url=base, headers={"X-Decider-Client": "web", "Origin": base},
                      timeout=90, trust_env=False) as client:
        client.post("/api/auth/login", json=json.loads(args.credentials.read_text())).raise_for_status()
        response = client.get("/api/gateway/effects/" + args.job_id)
        response.raise_for_status()
        job = response.json()
        if job["status"] != "completed" or len(job["results"]) != job["recorded_rows"]:
            raise ValueError("Require a complete, untruncated evaluation export")
        by_id = {r["id"]: r for r in job["results"]}
        seen = set()
        for review in planned:
            row = by_id[review["row_id"]]
            if not row.get("output", {}).get("complete"):
                raise ValueError("Cannot review an incomplete target output")
            if row["observation_id"] in seen:
                raise ValueError("One review per unique observation is required")
            seen.add(row["observation_id"])
        for review in planned:
            row = by_id[review["row_id"]]
            payload = {k: review[k] for k in ("status", "reason", "method")}
            if row.get("review"):
                if any(row["review"].get(k) != v for k, v in payload.items()):
                    raise ValueError("Existing review differs; refusing to overwrite")
                recorded = row
            else:
                response = client.post(
                    f"/api/gateway/effects/{job['id']}/reviews/{row['id']}", json=payload,
                )
                response.raise_for_status()
                recorded = response.json()
            audit.append({k: recorded[k] for k in ("id", "case_id", "observation_id", "review")})
            args.output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
            if len(audit) % 10 == 0:
                print(json.dumps({"recorded": len(audit), "planned": len(planned)}), flush=True)
    print(json.dumps({"recorded": len(audit), "new_model_calls": 0}))


if __name__ == "__main__":
    main()
