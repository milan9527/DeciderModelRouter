"""Compile real task outcomes into a candidate calibration artifact; no model calls."""

import argparse
import json
from pathlib import Path

from app.routing import Task, exclusion, load_catalog
from app.routing_calibration import build_calibration
from app.routing_policy import WORKFLOWS, load_rankings
from app.routing_selector import configurations


def known_configurations():
    catalog, rankings = load_catalog(), load_rankings()
    rows = {}
    for workflow in WORKFLOWS:
        pool = configurations(catalog, rankings, Task(text="calibration"), workflow, exclusion)
        for row in pool["candidates"]:
            rows.setdefault(
                row["candidate_id"],
                {
                    "configuration_id": row["candidate_id"],
                    "model_id": row["model"]["id"],
                    "invoke_id": row["model"]["invoke_id"],
                    "benchmark_configuration": row["benchmark"]["benchmark_configuration"],
                    "request_fields": row["benchmark"].get("request_fields", {}),
                    "workflows": [],
                },
            )["workflows"].append(workflow)
    return catalog, rankings, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--export-configurations", action="store_true")
    parser.add_argument("--minimum-samples", type=int, default=30)
    parser.add_argument("--ttl-days", type=int, default=30)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output path; do not overwrite historical evidence")
    catalog, rankings, known = known_configurations()
    if args.export_configurations:
        result = {
            "catalog_version": catalog["version"],
            "ranking_version": rankings["version"],
            "configurations": list(known.values()),
        }
    else:
        if not args.observations:
            parser.error("--observations is required")
        envelope = json.loads(args.observations.read_text())
        if (
            envelope["ranking_version"] != rankings["version"]
            or envelope["catalog_version"] != catalog["version"]
        ):
            parser.error("Observations belong to another ranking/configuration snapshot")
        result = build_calibration(
            envelope["observations"],
            known,
            rankings,
            catalog_version=catalog["version"],
            minimum_samples=args.minimum_samples,
            ttl_days=args.ttl_days,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({"output": str(args.output), "version": result.get("version"), "model_calls": 0})
    )


if __name__ == "__main__":
    main()
