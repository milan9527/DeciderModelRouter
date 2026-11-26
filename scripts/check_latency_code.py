"""Check every generated function variant in a credential-free Docker container."""

import argparse
import ast
import hashlib
import subprocess
from pathlib import Path

from scripts.check_effect_code import CASES, CHECKER
from scripts.check_paired_code import NEW, NEW_CHECKER, isolated, sources
from scripts.run_profile_quality import read, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    image = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True).strip()
    for cid, name in {**CASES, **NEW}.items():
        path = args.input / "base" / (cid + ".json.gz")
        target = args.input / "code-checks" / (cid + ".json")
        if not path.exists() or target.exists():
            continue
        row = read(path)
        if row["status"] != "completed":
            continue
        text = row["output"]["text"]
        try:
            ast.parse(text)
            bare = "```" not in text
        except SyntaxError:
            bare = False
        variants = [
            isolated(s, name, NEW_CHECKER if cid in NEW else CHECKER, image)
            for s in sources(text, name)
        ]
        functional = all(v["status"] == "pass" for v in variants)
        record = {
            "case_id": cid, "status": "pass" if functional and bare else "fail",
            "source": "isolated_functional_tests", "bare_code_format": bare,
            "functional_pass": functional, "variants": variants, "image": image,
            "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        save(target, record)
        print(cid, record["status"], "functional", functional, flush=True)


if __name__ == "__main__":
    main()
