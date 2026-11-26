"""Prewarm precision-preserving compilation on the existing GPU, with rollback."""

import argparse
import base64
import gzip
import hashlib
import json
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = "i-01180cf02d12e6e31"


def run(ssm, script, output, label, *, timeout=600):
    command = ssm.send_command(
        InstanceIds=[INSTANCE], DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["python3 - <<'LIQUID_UPDATE'\n" + script + "\nLIQUID_UPDATE"]},
        TimeoutSeconds=timeout,
    )["Command"]["CommandId"]
    (output / f"{label}-command.json").write_text(json.dumps({"command_id": command}))
    while True:
        time.sleep(2)
        try:
            result = ssm.get_command_invocation(CommandId=command, InstanceId=INSTANCE)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if result["Status"] not in {"Pending", "InProgress", "Delayed"}:
            break
    (output / f"{label}-result.json").write_text(json.dumps(result, indent=2))
    if result["Status"] != "Success":
        raise RuntimeError(f"{label}: {result['Status']}; inspect the saved SSM result")
    return result["StandardOutputContent"].strip()


def activation(staging, cache_seed):
    return r'''
import hashlib,json,pathlib,shutil,subprocess,time,urllib.request
root=pathlib.Path('/opt/decider-open-models')
staging=pathlib.Path(STAGING)
cache=root/'cache'/'liquid-inductor-preserve-v1'
cache.mkdir(parents=True,exist_ok=True)
if CACHE_SEED:
    shutil.copytree(CACHE_SEED,cache,dirs_exist_ok=True)
backup=staging/'rollback'
backup.mkdir(exist_ok=False)
files=[]
for path in [root/'source'/'liquid_server.py',root/'source'/'gateway.py',
             pathlib.Path('/etc/systemd/system/dmr-liquid.service')]:
    saved=backup/str(len(files))
    shutil.copy2(path,saved)
    files.append({'path':str(path),'backup':str(saved)})
(backup/'manifest.json').write_text(json.dumps(files,indent=2))
def ready(compiled,seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:18801/health',timeout=2) as response:
                health=json.load(response)
            if health.get('model')=='LiquidAI/d1-2B' and (
                not compiled or health.get('inference_engine')=='compiled_selection_eager_assessment_bf16'
            ):
                return health
        except (OSError,ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('Liquid readiness deadline exceeded')
try:
    for name in ['liquid_server.py','gateway.py']:
        shutil.copy2(staging/name,root/'source'/name)
    unit=pathlib.Path('/etc/systemd/system/dmr-liquid.service')
    text=unit.read_text()
    if '--compile' in text or text.count('--model /bench/models/liquid')!=1:
        raise RuntimeError('Unexpected existing Liquid unit; no blind replacement')
    warmup='/bench/'+str(staging.relative_to(root))+'/warmup.json'
    text=text.replace('--memory=4g','--memory=6g')
    text=text.replace('--model /bench/models/liquid',
        '--model /bench/models/liquid --compile --warmup-inputs '+warmup+
        ' --compile-cache /bench/cache/liquid-inductor-preserve-v1')
    unit.write_text(text)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','restart','dmr-liquid.service'],check=True)
    health=ready(True,360)
    inputs=json.loads((staging/'warmup.json').read_text())
    probes=[next(p for p in inputs.values() if 'action' not in p['questions']),
            next(p for p in inputs.values() if set(p['questions'])=={'action'})]
    for payload in probes:
        request=urllib.request.Request('http://127.0.0.1:18801/v1/systemone',
            data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=20) as response:
            inference=json.load(response)
        if inference.get('forward_passes')!=1 or inference.get('model')!='LiquidAI/d1-2B':
            raise RuntimeError('Post-warmup inference verification failed')
    subprocess.run(['systemctl','restart','dmr-gateway.service'],check=True)
    result={'status':'compiled','instance':INSTANCE,'backup':str(backup),'health':health,
            'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [root/'source'/'liquid_server.py',root/'source'/'gateway.py']},
            'warmup_sha256':hashlib.sha256((staging/'warmup.json').read_bytes()).hexdigest()}
except BaseException:
    for item in reversed(files):
        shutil.copy2(item['backup'],item['path'])
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','restart','dmr-liquid.service','dmr-gateway.service'],check=True)
    health=ready(False,120)
    (staging/'result.json').write_text(json.dumps({'status':'rolled_back','health':health}))
    raise
(staging/'result.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
'''.replace("STAGING", repr(staging)).replace("CACHE_SEED", repr(cache_seed)).replace("INSTANCE", repr(INSTANCE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmup-inputs", type=Path, required=True)
    parser.add_argument("--cache-seed", help="Previously verified Inductor cache directory on the GPU")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    warmup = json.loads(args.warmup_inputs.read_text())
    if not isinstance(warmup, dict) or not warmup or any(
        set(p) != {"state", "questions"} for p in warmup.values()
    ):
        raise ValueError("Expected nonempty named System One warmup requests")
    args.output.mkdir(parents=True, exist_ok=True)
    files = {
        **{name: (ROOT / "deployment/open_models" / name).read_text()
           for name in ("liquid_server.py", "gateway.py")},
        "warmup.json": json.dumps(warmup, ensure_ascii=False),
    }
    raw = gzip.compress(json.dumps(files).encode())
    sha = hashlib.sha256(raw).hexdigest()
    staging = f"/opt/decider-open-models/releases/compiled-{time.time_ns()}"
    ssm = boto3.client("ssm", region_name="us-east-1")
    chunks = [raw[i:i + 32000] for i in range(0, len(raw), 32000)]
    for i, chunk in enumerate(chunks):
        source = (
            "import base64,pathlib\n"
            f"p=pathlib.Path({staging!r});p.mkdir(parents=True,exist_ok=True)\n"
            f"(p/'part-{i}').write_bytes(base64.b64decode({base64.b64encode(chunk).decode()!r}))"
        )
        run(ssm, source, args.output, f"upload-{i}", timeout=60)
    source = (
        "import pathlib,gzip,json,hashlib\n"
        f"p=pathlib.Path({staging!r})\n"
        f"raw=b''.join((p/f'part-{{i}}').read_bytes() for i in range({len(chunks)}))\n"
        f"assert hashlib.sha256(raw).hexdigest()=={sha!r}\n"
        "for name,text in json.loads(gzip.decompress(raw)).items():(p/name).write_text(text)\n"
    )
    run(ssm, source, args.output, "assemble", timeout=60)
    source = activation(staging, args.cache_seed)
    (args.output / "activation.py").write_text(source)
    result = run(ssm, source, args.output, "activate")
    (args.output / "deployment.json").write_text(result + "\n")
    print(result)


if __name__ == "__main__":
    main()
