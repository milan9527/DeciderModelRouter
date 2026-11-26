"""Update the existing independent GPU services; keep rollback copies on that host."""

import argparse
import base64
import json
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
INDEPENDENT_GPU = "i-01180cf02d12e6e31"


def command():
    sources = {name: base64.b64encode((ROOT / "deployment/open_models" / name).read_bytes()).decode()
               for name in ("gateway.py", "liquid_server.py")}
    return """
import base64,hashlib,json,pathlib,shutil,subprocess,time
root=pathlib.Path('/opt/decider-open-models')
backup=root/'backups'/('joint-'+str(int(time.time())))
backup.mkdir(parents=True,exist_ok=False)
files=[]
def replace(path,raw):
    path=pathlib.Path(path)
    saved=backup/str(len(files))
    shutil.copy2(path,saved)
    files.append({'path':str(path),'backup':str(saved)})
    path.write_bytes(raw)
for name,encoded in SOURCES.items():
    replace(root/'source'/name,base64.b64decode(encoded))
# The backbone supports this context. This service configuration change still needs
# real endpoint validation; it is not a claim of evaluation accuracy at longer lengths.
path=pathlib.Path('/opt/decider-model/decider/config.json')
config=json.loads(path.read_text())
config['max_length']=24576
replace(path,(json.dumps(config,indent=2)+'\\n').encode())
export_path=path.with_name('export.json')
export=json.loads(export_path.read_text())
previous_hash=export['decider_sha256']['config.json']
new_hash=hashlib.sha256(path.read_bytes()).hexdigest()
export['decider_sha256']['config.json']=new_hash
export['files_sha256']['decider/config.json']=new_hash
export['deployment_context_override']={'previous_config_sha256':previous_hash,
    'max_length':24576,'purpose':'one-request full-candidate evidence routing; weights unchanged'}
replace(export_path,(json.dumps(export,indent=2)+'\\n').encode())
for name in ('dmr-decider.service','dmr-h2o.service','dmr-liquid.service'):
    path=pathlib.Path('/etc/systemd/system')/name
    text=path.read_text()
    if name=='dmr-decider.service':
        text=text.replace('--max-num-seqs 1 ', '--max-num-seqs 8 ')
        text=text.replace('--max-inflight-questions 1 ', '--max-inflight-questions 8 ')
        text=text.replace('--max-num-batched-tokens 8192 ', '--max-num-batched-tokens 24576 ')
        text=text.replace('--max-num-batched-tokens 16384 ', '--max-num-batched-tokens 24576 ')
    elif name=='dmr-h2o.service':
        text=text.replace('MAX_LEN=4096', 'MAX_LEN=16384')
        text=text.replace('GPU_UTIL=0.34', 'GPU_UTIL=0.36')
        text=text.replace('--max-num-seqs 1 ', '--max-num-seqs 8 ')
        if '--kv-cache-memory-bytes' not in text:
            text=text.replace('--enforce-eager','--kv-cache-memory-bytes 1073741824 --enforce-eager')
        text=text.replace('dmr-decider.service','dmr-liquid.service')
        text=text.replace('wait_ready.py 8100','wait_ready.py 18801')
    else:
        text=text.replace('dmr-h2o.service','dmr-decider.service')
        text=text.replace('wait_ready.py 18803','wait_ready.py 8100')
    replace(path,text.encode())
(backup/'manifest.json').write_text(json.dumps(files,indent=2))
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','restart','--no-block','dmr-decider','dmr-h2o','dmr-liquid','dmr-gateway'],check=True)
print(json.dumps({'backup':str(backup),'instance':'i-01180cf02d12e6e31',
                  'decider_context':24576,'h2o_context':16384,'files':len(files)}))
""".replace("SOURCES", repr(sources))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ssm = boto3.client("ssm", region_name="us-east-1")
    source = command()
    args.output.mkdir(exist_ok=True, parents=True)
    (args.output / "gpu-update-source.py").write_text(source)
    response = ssm.send_command(
        InstanceIds=[INDEPENDENT_GPU], DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["python3 - <<'JOINT_GPU_UPDATE'\n" + source + "\nJOINT_GPU_UPDATE"]},
        Comment="Enable single-request evidence routing on the independent GPU",
    )
    result = {"command_id": response["Command"]["CommandId"], "instance_id": INDEPENDENT_GPU}
    (args.output / "gpu-update-command.json").write_text(json.dumps(result))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
