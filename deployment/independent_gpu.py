"""Launch an independent GPU from a private runtime image and rotate service credentials."""

import argparse
import base64
import json
import secrets
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from deployment.independent_gateway import NAME, REGION, TAGS


def bootstrap(secret_arn, source_instance):
    gateway = base64.b64encode(
        (Path(__file__).parent / "open_models/gateway.py").read_bytes()
    ).decode()
    return f"""#!/bin/bash
set -euo pipefail
umask 077
token=$(curl -fsS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
instance=$(curl -fsS http://169.254.169.254/latest/meta-data/instance-id -H "X-aws-ec2-metadata-token: $token")
test -n "$instance"
test "$instance" != "{source_instance}"
systemctl stop decider-comparison-gateway decider-liquid decider-h2o decider-vllm || true
systemctl disable decider-comparison-gateway decider-liquid decider-h2o decider-vllm
for container in decider-comparison-gateway decider-liquid-resident decider-h2o-resident decider-vllm-service; do
  docker update --restart=no "$container" >/dev/null 2>&1 || true
  docker rm -f "$container" >/dev/null 2>&1 || true
done
if ! python3 -c 'import boto3' 2>/dev/null; then
  apt-get update -q
  apt-get install -y --no-install-recommends python3-boto3
fi
mkdir -p /opt/decider-secrets
python3 - <<'PY'
import boto3,pathlib,os,base64
key=boto3.client("secretsmanager",region_name="{REGION}").get_secret_value(SecretId="{secret_arn}")["SecretString"]
p=pathlib.Path("/opt/decider-secrets/api-key")
p.write_text(key)
p.chmod(0o600)
pathlib.Path("/opt/decider-open-models/source/gateway.py").write_bytes(base64.b64decode("{gateway}"))
PY
export DMR_INSTANCE="$instance"
python3 - <<'PY'
import pathlib,subprocess,os
units={{
 "decider-vllm":"dmr-decider",
 "decider-h2o":"dmr-h2o",
 "decider-liquid":"dmr-liquid",
 "decider-comparison-gateway":"dmr-gateway",
}}
containers={{
 "decider-vllm-service":"dmr-decider",
 "decider-h2o-resident":"dmr-h2o",
 "decider-liquid-resident":"dmr-liquid",
 "decider-comparison-gateway":"dmr-gateway",
}}
for old,new in units.items():
 path=subprocess.check_output(["systemctl","show",old+".service","-p","FragmentPath","--value"],text=True).strip()
 text=pathlib.Path(path).read_text()
 for before,after in units.items():text=text.replace(before+".service",after+".service")
 for before,after in containers.items():text=text.replace(before,after)
 if old=="decider-comparison-gateway":
  text=text.replace("--network host", "--network host -v /opt/decider-secrets/api-key:/run/secrets/router-key:ro -e OPEN_MODELS_API_KEY_FILE=/run/secrets/router-key")
  text=text.replace("{source_instance}",os.environ["DMR_INSTANCE"])
 pathlib.Path("/etc/systemd/system/"+new+".service").write_text(text)
PY
systemctl daemon-reload
systemctl enable dmr-decider dmr-h2o dmr-liquid dmr-gateway
systemctl start --no-block dmr-decider dmr-h2o dmr-liquid dmr-gateway
touch /opt/decider-open-models/independent-bootstrap-complete
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-instance", required=True)
    args = parser.parse_args()
    session = boto3.Session(region_name=REGION)
    ec2, iam, sm = session.client("ec2"), session.client("iam"), session.client("secretsmanager")
    path = args.output / "gpu.json"
    state = json.loads(path.read_text()) if path.exists() else {}

    def save():
        path.write_text(json.dumps(state, indent=2) + "\n")
        path.chmod(0o600)

    while True:
        resources = json.loads((args.output / "resources.json").read_text())
        if "gpu_profile" in resources:
            break
        print("Waiting for independent GPU IAM/network resources...", flush=True)
        time.sleep(10)
    if "secret_arn" not in state:
        try:
            result = sm.create_secret(
                Name="DeciderModelRouter/gpu-service-key",
                SecretString=secrets.token_urlsafe(40),
                Description="Private GPU HTTP authentication for DeciderModelRouter",
                Tags=TAGS,
            )
            state["secret_arn"] = result["ARN"]
        except sm.exceptions.ResourceExistsException:
            state["secret_arn"] = sm.describe_secret(SecretId="DeciderModelRouter/gpu-service-key")[
                "ARN"
            ]
        save()
    for suffix in ("-gpu", "-execution"):
        iam.put_role_policy(
            RoleName=NAME + suffix,
            PolicyName="DedicatedGPUSecret",
            PolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": "secretsmanager:GetSecretValue",
                            "Resource": state["secret_arn"],
                        }
                    ],
                }
            ),
        )
    if "instance_id" not in state:
        while True:
            image = ec2.describe_images(ImageIds=[resources["gpu_image"]])["Images"][0]
            if image["State"] == "available":
                break
            if image["State"] == "failed":
                raise RuntimeError("Private GPU image creation failed")
            print(
                "Waiting for the private runtime image (source GPU remains running)...", flush=True
            )
            time.sleep(15)
        for i in (1, 0):
            try:
                instance = ec2.run_instances(
                    ImageId=resources["gpu_image"],
                    InstanceType="g6.xlarge",
                    MinCount=1,
                    MaxCount=1,
                    SubnetId=resources[f"private_subnet_{i}"],
                    SecurityGroupIds=[resources["gpu_sg"]],
                    IamInstanceProfile={"Arn": resources["gpu_profile"]},
                    MetadataOptions={"HttpTokens": "required", "HttpPutResponseHopLimit": 2},
                    BlockDeviceMappings=[
                        {
                            "DeviceName": image["RootDeviceName"],
                            "Ebs": {
                                "VolumeSize": 160,
                                "VolumeType": "gp3",
                                "Encrypted": True,
                                "DeleteOnTermination": True,
                            },
                        }
                    ],
                    UserData=bootstrap(state["secret_arn"], args.source_instance),
                    ClientToken=resources["deployment_id"] + f"-gpu-{i}",
                    TagSpecifications=[
                        {
                            "ResourceType": "instance",
                            "Tags": TAGS + [{"Key": "Name", "Value": NAME + "-gpu"}],
                        },
                        {"ResourceType": "volume", "Tags": TAGS},
                    ],
                )["Instances"][0]
                state.update(
                    instance_id=instance["InstanceId"],
                    private_ip=instance["PrivateIpAddress"],
                    instance_type="g6.xlarge",
                    image_id=resources["gpu_image"],
                )
                save()
                break
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "InsufficientInstanceCapacity" or i == 0:
                    raise
    print(json.dumps({"phase": "gpu_launched", **state}))


if __name__ == "__main__":
    main()
