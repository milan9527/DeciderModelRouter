"""Credential-free isolated functional checks for base and new paired code tasks."""

import argparse
import ast
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from scripts.check_effect_code import CASES, CHECKER, limit_capture
from scripts.run_paired_quality import ARMS
from scripts.run_profile_quality import read, save

NEW = {"new-code-overlap": "overlaps", "new-code-events": "net",
       "new-code-clock": "active_days", "new-code-allocate": "allocate"}
NEW_CHECKER = r'''
import copy, json, random, pathlib, traceback
def reference(kind, args):
    if kind == "overlaps":
        a,b=args
        if a[0]>a[1] or b[0]>b[1]: raise ValueError()
        return max(a[0],b[0]) < min(a[1],b[1])
    if kind == "net":
        seen=set(); total=0
        for tenant,eid,amount in args[0]:
            key=(tenant,eid)
            if key not in seen: seen.add(key); total+=amount
        return total
    if kind == "active_days":
        total,pauses=args
        if total<0 or any(a>b for a,b in pauses): raise ValueError()
        return sum(not any(a<=d<b for a,b in pauses) for d in range(total))
    stock,orders=args
    if stock<0 or any(q<0 for oid,q in orders): raise ValueError()
    result={}
    for oid,q in orders:
        if oid not in result:
            result[oid]=min(stock,q); stock-=result[oid]
    return result
def run():
    scope={}
    exec(compile(pathlib.Path("/work/answer.py").read_text(),"answer.py","exec"),scope)
    kind=pathlib.Path("/work/kind").read_text(); fn=scope[kind]; rng=random.Random(261126)
    if kind=="overlaps":
        samples=[[(1,3),(3,5)],[(2,2),(1,4)],[(3,1),(0,5)],[(-4,-1),(-2,2)]]
        samples += [[tuple(rng.randrange(-8,9) for _ in range(2)) for _ in range(2)] for _ in range(160)]
    elif kind=="net":
        samples=[[[]],[[("A","E",100),("B","E",200),("A","E",-300),("A","R",-50)]]]
        samples += [[[(rng.choice("ABC"),rng.choice("XYZ"),rng.randrange(-200,300))
                     for _ in range(rng.randrange(40))]] for _ in range(160)]
    elif kind=="active_days":
        samples=[[12,[(2,5),(4,7),(2,5)]],[0,[]],[4,[(-10,30)]],[4,[(3,2)]],
                 [3,[(0,1),(1,3)]],[5,[(2,2)]]]
        samples += [[rng.randrange(-2,25),[tuple(rng.randrange(-5,30) for _ in range(2))
                      for _ in range(rng.randrange(10))]] for _ in range(160)]
    else:
        samples=[[35,[("O2",10),("O3",30),("O1",20)]],[3,[("A",2),("A",9),("B",3)]],
                 [0,[]],[-1,[]],[5,[("A",1),("A",-2)]]]
        samples += [[rng.randrange(-2,30),[(rng.choice("ABCDE"),rng.randrange(-1,15))
                      for _ in range(rng.randrange(15))]] for _ in range(160)]
    failures=[]
    for i,args in enumerate(samples):
        before=copy.deepcopy(args); expected_error=None; actual_error=None; value=None
        try: expected=reference(kind,args)
        except Exception as exc: expected_error=type(exc).__name__; expected=None
        try: value=fn(*args)
        except Exception as exc: actual_error=type(exc).__name__
        ok=(args==before and actual_error==expected_error and
            (expected_error is not None or type(value) is type(expected) and value==expected))
        if not ok: failures.append({"index":i,"input":before,"expected":expected,
                 "expected_error":expected_error,"actual":repr(value)[:400],"actual_error":actual_error,
                 "input_unchanged":args==before})
    return {"tests":len(samples),"passed":len(samples)-len(failures),"failures":failures[:8]}
try: result=run()
except BaseException as exc: result={"tests":0,"passed":0,"error":type(exc).__name__,
                                    "detail":traceback.format_exc(limit=2)}
print("\nEFFECT_TEST_RESULT="+json.dumps(result))
'''


def sources(text, name):
    """Preserve all variants rather than selecting the best-looking implementation."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    matching = [s for s in blocks if re.search(rf"\bdef\s+{re.escape(name)}\s*\(", s)]
    return matching or [text]


def isolated(source, kind, checker, image):
    with tempfile.TemporaryDirectory(prefix="paired-code-") as temp:
        folder = Path(temp)
        folder.chmod(0o755)
        for name, data in [("answer.py", source), ("kind", kind), ("check.py", checker)]:
            (folder / name).write_text(data)
            (folder / name).chmod(0o444)
        container = "paired-code-" + folder.name
        cmd = ["docker", "run", "--rm", "--name", container, "--pull", "never", "--log-driver", "none",
               "--network", "none", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--pids-limit", "32",
               "--memory", "128m", "--memory-swap", "128m", "--cpus", "0.5", "--user", "65534:65534",
               "--ulimit", "nofile=64:64", "--ulimit", "fsize=1048576:1048576",
               "--tmpfs", "/tmp:rw,noexec,nosuid,size=8m",
               "--mount", f"type=bind,source={folder},target=/work,readonly",
               "--entrypoint", "python", image, "-I", "-B", "/work/check.py"]
        try:
            with (folder / "stdout").open("w") as out, (folder / "stderr").open("w") as err:
                result = subprocess.run(cmd, stdout=out, stderr=err, timeout=15, preexec_fn=limit_capture)
            marker = (folder / "stdout").read_text()[:65536].rsplit("\nEFFECT_TEST_RESULT=", 1)
            value = json.loads(marker[1]) if len(marker) == 2 else {"error": "no_test_result"}
            ok = result.returncode == 0 and value.get("tests", 0) > 0 and value["passed"] == value["tests"]
        except (ValueError, subprocess.TimeoutExpired) as exc:
            value, ok = {"error": type(exc).__name__}, False
        finally:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    return {"status": "pass" if ok else "fail", "result": value,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    image = subprocess.check_output(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"],
                                    text=True).strip()
    for arm in ARMS:
        for cid, kind in {**CASES, **NEW}.items():
            group = "holdout" if cid in NEW else "base"
            path = args.input / arm / group / (cid + ".json.gz")
            output = args.input / arm / "code-checks" / (cid + ".json")
            if output.exists() or not path.exists():
                continue
            row = read(path)
            if row["status"] != "completed":
                continue
            text = row["output"]["text"]
            try:
                ast.parse(text)
                bare_code = "```" not in text
            except SyntaxError:
                bare_code = False
            variants = [isolated(s, kind, NEW_CHECKER if cid in NEW else CHECKER, image) for s in sources(text, kind)]
            functional = all(r["status"] == "pass" for r in variants)
            record = {"case_id": cid, "method": "isolated_functional_tests",
                      "status": "pass" if functional and bare_code else "fail",
                      "bare_code_format": bare_code, "functional_pass": functional,
                      "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                      "image": image, "variants": variants,
                      "reason": "检查实际函数、类型、异常及输入不变；裸代码格式另计，全部版本均须通过。"}
            save(output, record)
            print(arm, cid, record["status"], "functional", functional, flush=True)


if __name__ == "__main__":
    main()
