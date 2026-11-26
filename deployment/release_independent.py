"""Publish the dedicated service and seed its independent self-managed administrator."""

import argparse
import json
import secrets
import time
from pathlib import Path

import boto3

from app.auth import DynamoStore, save_user
from deployment.deploy_gateway import ROOT, Publisher, files
from deployment.independent_gateway import NAME, REGION, TAGS


def task_definition(resources, gpu, image):
    origin = "https://" + resources["distribution"]["DomainName"]
    environment = {
        "AWS_REGION": REGION,
        "BEDROCK_ENABLED": "true",
        "SERVE_FRONTEND": "false",
        "AUTH_TABLE": resources["auth_table"],
        "GATEWAY_DATA_TABLE": resources["data_table"],
        "GATEWAY_MEDIA_BUCKET": resources.get("media_bucket", ""),
        "AUTH_COOKIE_SECURE": "true",
        "AUTH_ORIGIN": origin,
        "GPU_PROVIDER": "vllm",
        "GATEWAY_DECISION_PROVIDER": "liquid",
        "GATEWAY_DECISION_MODE": "two_stage",
        "GATEWAY_DECIDER_URL": f"http://{gpu['private_ip']}:8100" if gpu else "",
        "GATEWAY_DECIDER_MODEL": "strands-decider-2B-hobson-v19-vllm",
        "OPEN_MODELS_URL": f"http://{gpu['private_ip']}:18124" if gpu else "",
    }
    return {
        "family": NAME,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "512",
        "memory": "1024",
        "runtimePlatform": {"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
        "taskRoleArn": resources["task_role"],
        "executionRoleArn": resources["execution_role"],
        "containerDefinitions": [
            {
                "name": "api",
                "image": image,
                "essential": True,
                "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
                "readonlyRootFilesystem": True,
                "linuxParameters": {"initProcessEnabled": True},
                "environment": [{"name": k, "value": v} for k, v in environment.items()],
                "secrets": [
                    {"name": name, "valueFrom": gpu["secret_arn"]}
                    for name in ("OPEN_MODELS_API_KEY", "DECIDER_API_KEY")
                ]
                if gpu
                else [],
                "healthCheck": {
                    "command": [
                        "CMD-SHELL",
                        "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=3)\"",
                    ],
                    "interval": 20,
                    "timeout": 5,
                    "retries": 3,
                    "startPeriod": 30,
                },
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": "/" + NAME + "/api",
                        "awslogs-region": REGION,
                        "awslogs-stream-prefix": "api",
                    },
                },
            }
        ],
        "tags": [{"key": t["Key"], "value": t["Value"]} for t in TAGS],
    }


def seed_admin(resources, directory, session):
    sm = session.client("secretsmanager")
    name = "DeciderModelRouter/admin-credentials"
    try:
        secret = sm.get_secret_value(SecretId=name)
        credentials = json.loads(secret["SecretString"])
        arn = secret["ARN"]
    except sm.exceptions.ResourceNotFoundException:
        credentials = {"username": "admin", "password": secrets.token_urlsafe(24)}
        arn = sm.create_secret(
            Name=name,
            SecretString=json.dumps(credentials),
            Tags=TAGS,
            Description="Initial self-managed DeciderModelRouter administrator; not Cognito",
        )["ARN"]
    store = DynamoStore(resources["auth_table"], REGION)
    if not store.get("USER#" + credentials["username"]):
        save_user(store, credentials["username"], credentials["password"], role="admin")
    path = directory / "admin-credentials.json"
    if not path.exists():
        path.write_text(json.dumps(credentials) + "\n")
        path.chmod(0o600)
    return arn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", required=True, type=Path)
    stage = parser.add_mutually_exclusive_group(required=True)
    stage.add_argument("--gpu", type=Path)
    stage.add_argument(
        "--prepare",
        action="store_true",
        help="Publish auth/history/UI while GPU is provisioning; all classifiers remain unconfigured",
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    resources = json.loads(args.resources.read_text())
    gpu = json.loads(args.gpu.read_text()) if args.gpu else None
    assert resources["project"] == NAME and resources["cluster"] == NAME
    if gpu:
        assert gpu["instance_id"].startswith("i-")
        assert gpu["instance_id"] != "i-0ae7eb0abcb815af3"
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    cfg = {
        "region": REGION,
        "cluster": NAME,
        "service": NAME,
        "bucket": resources["bucket"],
        "distribution_id": resources["distribution"]["Id"],
    }
    session = boto3.Session(region_name=REGION)
    publisher = Publisher(cfg, session)
    image = publisher.image(args.image)
    existing = publisher.ecs.describe_services(cluster=NAME, services=[NAME])["services"]
    previous = next((s["taskDefinition"] for s in existing if s["status"] == "ACTIVE"), None)
    definition = task_definition(resources, gpu, image)
    task = publisher.ecs.register_task_definition(**definition)["taskDefinition"][
        "taskDefinitionArn"
    ]
    deploy = {
        "previous_task_definition": previous,
        "task_definition": task,
        "image": image,
        "gpu_instance": gpu["instance_id"] if gpu else None,
        "gpu_configured": bool(gpu),
        "started_at": int(time.time()),
    }
    (args.output / "api-release.json").write_text(json.dumps(deploy, indent=2) + "\n")
    if previous:
        publisher.ecs.update_service(cluster=NAME, service=NAME, taskDefinition=task)
    else:
        publisher.ecs.create_service(
            cluster=NAME,
            serviceName=NAME,
            taskDefinition=task,
            desiredCount=1,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": [resources[f"private_subnet_{i}"] for i in range(2)],
                    "securityGroups": [resources["api_sg"]],
                    "assignPublicIp": "DISABLED",
                }
            },
            loadBalancers=[
                {
                    "targetGroupArn": resources["target_group"],
                    "containerName": "api",
                    "containerPort": 8000,
                }
            ],
            healthCheckGracePeriodSeconds=90,
            deploymentConfiguration={
                "maximumPercent": 200,
                "minimumHealthyPercent": 100,
                "deploymentCircuitBreaker": {"enable": True, "rollback": True},
            },
            tags=[{"key": t["Key"], "value": t["Value"]} for t in TAGS],
        )
    publisher.wait_service(task)
    deploy["admin_secret"] = seed_admin(resources, args.output, session)
    assets = files(ROOT / "frontend/gateway")
    publisher.preflight(assets)
    record = publisher.publish(
        assets,
        None,
        task,
        args.output,
        resources["distribution"]["DomainName"],
    )
    deploy.update(phase="complete", url=record["url"], invalidation_id=record["invalidation_id"])
    (args.output / "api-release.json").write_text(json.dumps(deploy, indent=2) + "\n")
    print(json.dumps(deploy, ensure_ascii=False))


if __name__ == "__main__":
    main()
