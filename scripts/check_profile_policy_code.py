"""Diagnose both interval-merge implementations in the experimental target answer."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from scripts.benchmark_profile_policy import read_json, write_json
from scripts.check_effect_code import limit_capture

CHECKER = r'''
import copy,json,pathlib,random
scope={}
exec(compile(pathlib.Path("/work/answer.py").read_text(),"answer.py","exec"),scope)
def reference(xs):
    result=[]
    for a,b in sorted(xs):
        if result and a<=result[-1][1]: result[-1][1]=max(b,result[-1][1])
        else: result.append([a,b])
    return result
rng=random.Random(261126)
cases=[[],[[1,3],[3,4]],[[5,8],[1,7]],[[1,10],[2,3]],[[1,2],[4,5]]]
for _ in range(100):
    xs=[]
    for _ in range(rng.randrange(30)):
        a=rng.randrange(-20,20);xs.append([a,a+rng.randrange(10)])
    cases.append(xs)
reports=[]
for name in ("merge_intervals","merge_intervals_refined"):
    failures=[]
    for i,xs in enumerate(cases):
        try:
            actual=[list(x) for x in scope[name](copy.deepcopy(xs))]
            expected=reference(xs)
            if actual!=expected: failures.append({"index":i,"input":xs,"expected":expected,"actual":actual})
        except BaseException as exc: failures.append({"index":i,"error":type(exc).__name__})
    reports.append({"function":name,"tests":len(cases),"passed":len(cases)-len(failures),"failures":failures[:5]})
print("\nPROFILE_CODE_RESULT="+json.dumps(reports))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--image", required=True, help="Existing local Python image; never pulled")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    row = next(r for r in read_json(args.targets)["rows"] if r["case"] == "coding")
    # Preserve the two function implementations unchanged; prose/examples are not executable code.
    blocks = re.findall(r"```python\s*\n(.*?)```", row["text"], re.S)
    source = "\n\n".join(b for b in blocks if re.search(r"^def merge_intervals", b, re.M))
    if not source:
        raise ValueError("No interval-merge implementation to check")
    image = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="profile-code-") as temp:
        path = Path(temp)
        path.chmod(0o755)
        for name, text in (("answer.py", source), ("check.py", CHECKER)):
            (path / name).write_text(text)
            (path / name).chmod(0o444)
        container = "profile-code-" + hashlib.sha256(temp.encode()).hexdigest()[:12]
        command = [
            "docker", "run", "--rm", "--name", container, "--pull", "never", "--log-driver", "none",
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "32",
            "--memory", "128m", "--memory-swap", "128m", "--cpus", "0.5",
            "--user", "65534:65534", "--ulimit", "nofile=64:64", "--ulimit", "fsize=1048576:1048576",
            "--mount", f"type=bind,source={path},target=/work,readonly",
            "--entrypoint", "python", image, "-I", "-B", "/work/check.py",
        ]
        try:
            with (path / "stdout").open("w") as out, (path / "stderr").open("w") as err:
                run = subprocess.run(command, stdout=out, stderr=err, timeout=15, preexec_fn=limit_capture)
            if run.returncode:
                raise RuntimeError((path / "stderr").read_text()[:2000])
            reports = json.loads((path / "stdout").read_text().rsplit("\nPROFILE_CODE_RESULT=", 1)[1])
        finally:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    report = {
        "case": "coding", "model_id": row["model_id"], "image": image,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "boundary": "closed interval semantics; return values checked, input immutability not required",
        "method": "post-generation functional diagnostics, not hidden held-out evaluation",
        "isolation": "no network/credentials, read-only, non-root, 128MB, 0.5CPU, 15s limit",
        "results": reports,
    }
    write_json(args.output, report)
    print(json.dumps([{k: r[k] for k in ("function", "tests", "passed")} for r in reports]))


if __name__ == "__main__":
    main()
