"""Run generated evaluation code inside a constrained, credential-free Docker container."""

import argparse
import hashlib
import json
import resource
import subprocess
import tempfile
from pathlib import Path

import httpx

CASES = {"python-dedup": "unique", "python-brackets": "valid", "python-merge": "merge"}


def limit_capture():
    resource.setrlimit(resource.RLIMIT_FSIZE, (1_048_576, 1_048_576))


CHECKER = r'''
import copy, json, random, traceback
import pathlib

def run():
    scope = {}
    # This exec is inside the disposable container, never in the gateway or host process.
    exec(compile(pathlib.Path("/work/answer.py").read_text(), "answer.py", "exec"), scope)
    kind = pathlib.Path("/work/kind").read_text()
    rng = random.Random(241125)
    samples = []
    if kind == "unique":
        samples = [([], []), ([1, 1, 2, 1], [1, 2]), (["b", "a", "b"], ["b", "a"])]
        for _ in range(100):
            xs = [rng.randrange(10) for _ in range(rng.randrange(80))]
            samples.append((xs, list(dict.fromkeys(xs))))
    elif kind == "valid":
        def reference(s):
            stack = []
            for ch in s:
                if ch in "([{": stack.append(ch)
                elif not stack or stack.pop() != {")":"(", "]":"[", "}":"{"}[ch]: return False
            return not stack
        words = ["", "()", "([]{})", "([)]", "]", "(((", "()" * 200, "(" * 100 + ")" * 100]
        words += ["".join(rng.choice("()[]{}") for _ in range(rng.randrange(50))) for _ in range(100)]
        samples = [(s, reference(s)) for s in words]
    else:
        def reference(xs):
            result = []
            for a, b in sorted(xs):
                if result and a <= result[-1][1]: result[-1][1] = max(b, result[-1][1])
                else: result.append([a, b])
            return result
        arrays = [[], [[1, 3], [3, 4]], [[5, 8], [1, 7]], [[1, 10], [2, 3]], [[1, 1]]]
        for _ in range(100):
            xs = []
            for _ in range(rng.randrange(30)):
                a = rng.randrange(-20, 20); xs.append([a, a + rng.randrange(10)])
            arrays.append(xs)
        samples = [(xs, reference(xs)) for xs in arrays]
    failures = []
    fn = scope[kind]
    for i, (given, expected) in enumerate(samples):
        original = copy.deepcopy(given)
        try:
            actual = fn(given)
            valid = type(actual) is type(expected) and actual == expected
            if kind == "merge": valid = valid and given == original
            if not valid: failures.append({"index": i, "input": original, "expected": expected, "actual": repr(actual)[:300]})
        except BaseException as exc:
            failures.append({"index": i, "error": type(exc).__name__})
    return {"tests": len(samples), "passed": len(samples)-len(failures), "failures": failures[:5]}

try:
    result = run()
except BaseException as exc:
    result = {"tests": 0, "passed": 0, "error": type(exc).__name__, "detail": traceback.format_exc(limit=2)}
print("\nEFFECT_TEST_RESULT=" + json.dumps(result))
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evaluation", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--image", required=True, help="Local Python container image, preferably pinned by digest")
    p.add_argument("--url")
    p.add_argument("--credentials", type=Path)
    args = p.parse_args()
    if bool(args.url) != bool(args.credentials):
        p.error("--url and --credentials must be provided together")
    job = json.loads(args.evaluation.read_text())
    if args.url and job["status"] != "completed":
        p.error("Only a completed evaluation may receive persisted reviews")
    image = subprocess.run(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"],
                           check=True, capture_output=True, text=True).stdout.strip()
    reports, seen = [], set()
    for row in job["results"]:
        oid = row.get("observation_id")
        if row["case_id"] not in CASES or not row.get("output", {}).get("complete") or oid in seen:
            continue
        seen.add(oid)
        source = row["output"].get("text", "")
        with tempfile.TemporaryDirectory(prefix="effect-code-") as temp:
            path = Path(temp)
            path.chmod(0o755)
            for name, content in (("answer.py", source), ("kind", CASES[row["case_id"]]), ("check.py", CHECKER)):
                (path / name).write_text(content)
                (path / name).chmod(0o444)
            name = "effect-code-" + oid
            command = [
                "docker", "run", "--rm", "--name", name, "--pull", "never", "--log-driver", "none",
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "32",
                "--memory", "128m", "--memory-swap", "128m", "--cpus", "0.5",
                "--user", "65534:65534", "--ulimit", "nofile=64:64",
                "--ulimit", "fsize=1048576:1048576",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=8m",
                "--mount", f"type=bind,source={path},target=/work,readonly",
                "--entrypoint", "python", image, "-I", "-B", "/work/check.py",
            ]
            try:
                # Bound both the container and the host CLI writing captured output.
                with (path / "stdout.log").open("w") as stdout, (path / "stderr.log").open("w") as stderr:
                    proc = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=15, preexec_fn=limit_capture)
                text = (path / "stdout.log").read_text()[:65536]
                marker = text.rsplit("\nEFFECT_TEST_RESULT=", 1)
                result = json.loads(marker[1]) if len(marker) == 2 else {"error": "no_test_result"}
                passed = proc.returncode == 0 and result.get("tests", 0) > 0 and result["passed"] == result["tests"]
            except (subprocess.TimeoutExpired, ValueError):
                result, passed = {"error": "timeout_or_invalid_result"}, False
            finally:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            reports.append({
                "row_id": row["id"], "observation_id": oid, "case_id": row["case_id"],
                "image": image, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "status": "pass" if passed else "fail", "result": result,
                "isolation": "network none; read-only; non-root; no credentials; 128MB; 0.5 CPU; 15s",
            })
    args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
    if args.url:
        base = args.url.rstrip("/")
        with httpx.Client(base_url=base, headers={"X-Decider-Client": "web", "Origin": base}, timeout=60, trust_env=False) as client:
            client.post("/api/auth/login", json=json.loads(args.credentials.read_text())).raise_for_status()
            for report in reports:
                reason = (
                    f"隔离容器功能测试：{report['case_id']}；{json.dumps(report['result'], ensure_ascii=False)}；"
                    f"source_sha256={report['source_sha256']}；image={image}。这是公开开发回归测试，不是独立隐藏基准。"
                )
                response = client.post(f"/api/gateway/effects/{job['id']}/reviews/{report['row_id']}",
                                       json={"status": report["status"], "reason": reason[:4000], "method": "isolated_tests"})
                response.raise_for_status()
    print(json.dumps({"observations": len(reports), "passed": sum(r["status"] == "pass" for r in reports)}))


if __name__ == "__main__":
    main()
