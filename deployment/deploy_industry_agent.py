"""Deploy this project's AgentCore V2 runtime and connect the independent ECS gateway."""

import argparse
import json
import time
from pathlib import Path

import boto3

from deployment.deploy_gateway import ROOT, Publisher, files, resources, task_revision

NAME = "decider_model_router_industry"
ROLE = "decider-model-router-agent"


def runtime_request(image, role, url):
    return {
        "agentRuntimeName": NAME,
        "agentRuntimeArtifact": {"containerConfiguration": {"containerUri": image}},
        "roleArn": role, "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"}, "platformVersion": "V2",
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 300, "maxLifetime": 3600},
        "environmentVariables": {"MODEL_ROUTER_URL": url, "OTEL_SDK_DISABLED": "true"},
        "description": "DeciderModelRouter industry agent; Strands custom Model Router provider; platform V2",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", required=True, type=Path)
    parser.add_argument("--agent-image", required=True)
    parser.add_argument("--api-image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cfg = resources(args.resources)
    if cfg["cluster"] != "decider-model-router" or cfg["service"] != "decider-model-router":
        raise ValueError("Only the independent Model Router service may be updated")
    args.output.mkdir(parents=True, exist_ok=False)
    session = boto3.Session(region_name=cfg["region"])
    pub = Publisher(cfg, session)
    assets = files(ROOT / "frontend/gateway")
    service, current, domain = pub.preflight(assets)
    agent_image, api_image = pub.image(args.agent_image), pub.image(args.api_image)
    iam, control = session.client("iam"), session.client("bedrock-agentcore-control")
    account = service["serviceArn"].split(":")[4]
    region = cfg["region"]
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": account},
                      "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{NAME}-*"}},
    }]}
    try:
        role = iam.get_role(RoleName=ROLE)["Role"]
        iam.update_assume_role_policy(RoleName=ROLE, PolicyDocument=json.dumps(trust))
    except iam.exceptions.NoSuchEntityException:
        role = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust),
                               Tags=[{"Key": "Project", "Value": "DeciderModelRouter"}])["Role"]
    repo = agent_image.split("/", 1)[1].split("@")[0]
    log_prefix = f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/{NAME}-*"
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
         "Resource": f"arn:aws:ecr:{region}:{account}:repository/{repo}"},
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
         "Resource": [log_prefix, log_prefix + ":*"]},
    ]}
    iam.put_role_policy(RoleName=ROLE, PolicyName="own-runtime-only", PolicyDocument=json.dumps(policy))
    existing = None
    for page in control.get_paginator("list_agent_runtimes").paginate():
        existing = next((r for r in page["agentRuntimes"] if r["agentRuntimeName"] == NAME), existing)
    request = runtime_request(agent_image, role["Arn"], "https://" + domain)
    if existing:
        request.pop("agentRuntimeName")
        request["agentRuntimeId"] = existing["agentRuntimeId"]
        response = control.update_agent_runtime(**request)
    else:
        # Allow propagation of the newly created execution role.
        time.sleep(10)
        response = control.create_agent_runtime(**request, tags={"Project": "DeciderModelRouter"})
    runtime_id, arn = response["agentRuntimeId"], response["agentRuntimeArn"]
    checkpoint = {"runtime_id": runtime_id, "runtime_arn": arn, "agent_image": agent_image, "api_image": api_image}
    (args.output / "runtime.json").write_text(json.dumps(checkpoint, indent=2) + "\n")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        runtime = control.get_agent_runtime(agentRuntimeId=runtime_id)
        if runtime["status"] == "READY":
            break
        if runtime["status"] not in {"CREATING", "UPDATING"}:
            raise RuntimeError("AgentCore runtime failed: " + runtime["status"])
        print("Waiting for AgentCore V2 runtime...", flush=True)
        time.sleep(10)
    else:
        raise TimeoutError("AgentCore runtime did not become ready")
    if runtime.get("platformVersion") != "V2":
        raise RuntimeError("AgentCore did not confirm platformVersion V2")
    checkpoint.update(platform_version=runtime["platformVersion"], status=runtime["status"],
                      runtime_version=runtime["agentRuntimeVersion"])
    (args.output / "runtime.json").write_text(json.dumps(checkpoint, indent=2) + "\n")
    task_role = current["taskDefinition"]["taskRoleArn"].rsplit("/", 1)[1]
    if task_role != "decider-model-router-task":
        raise ValueError("Unexpected ECS role; refusing to modify a shared role")
    iam.put_role_policy(RoleName=task_role, PolicyName="invoke-industry-agent",
                        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{
                            "Effect": "Allow", "Action": "bedrock-agentcore:InvokeAgentRuntime",
                            "Resource": [arn, arn + "/runtime-endpoint/*"],
                        }]}))
    allowed = pub.ecs.meta.service_model.operation_model("RegisterTaskDefinition").input_shape.members
    definition = task_revision({**current["taskDefinition"], "tags": current.get("tags", [])},
                               api_image, "api", "liquid", allowed)
    api = next(c for c in definition["containerDefinitions"] if c["name"] == "api")
    env = {e["name"]: e["value"] for e in api["environment"]}
    env.update(GATEWAY_AGENT_RUNTIME_ARN=arn, GATEWAY_AGENT_RUNTIME_QUALIFIER="DEFAULT")
    api["environment"] = [{"name": k, "value": v} for k, v in env.items()]
    (args.output / "previous-task.json").write_text(json.dumps(current, default=str, indent=2) + "\n")
    result = pub.publish(assets, definition, service["taskDefinition"], args.output, domain)
    print(json.dumps({"phase": result["phase"], **checkpoint}), flush=True)


if __name__ == "__main__":
    main()
