from pathlib import Path
from unittest.mock import MagicMock

from deployment.independent_gateway import NAME, Infrastructure
from deployment.independent_gpu import bootstrap
from deployment.release_independent import task_definition


def test_independent_task_has_dedicated_storage_gpu_origin_and_secret_references():
    resources = {
        "distribution": {"DomainName": "dedicated.cloudfront.net"},
        "auth_table": NAME + "-auth",
        "data_table": NAME + "-data",
        "task_role": "dedicated-task-role",
        "execution_role": "dedicated-execution-role",
    }
    gpu = {
        "private_ip": "10.146.11.5",
        "secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:dedicated",
    }
    task = task_definition(resources, gpu, "repo@sha256:" + "a" * 64)
    api = task["containerDefinitions"][0]
    env = {e["name"]: e["value"] for e in api["environment"]}
    assert env["AUTH_ORIGIN"] == "https://dedicated.cloudfront.net"
    assert env["AUTH_TABLE"] != env["GATEWAY_DATA_TABLE"]
    assert env["OPEN_MODELS_URL"] == "http://10.146.11.5:18124"
    assert env["GATEWAY_DECIDER_URL"] == "http://10.146.11.5:8100"
    assert all(s["valueFrom"] == gpu["secret_arn"] for s in api["secrets"])
    assert "DECIDER_API_KEY" not in env
    assert task["taskRoleArn"] == "dedicated-task-role"
    assert task["runtimePlatform"]["cpuArchitecture"] == "ARM64"
    assert "gateway_main:app" in Path("deployment/gateway.Dockerfile").read_text()


def test_runtime_image_creation_never_reboots_source_and_is_checkpointed(tmp_path):
    infra = Infrastructure.__new__(Infrastructure)
    infra.state = {"deployment_id": "unique-deployment"}
    infra.file = tmp_path / "resources.json"
    infra.ec2 = MagicMock()
    infra.ec2.create_image.return_value = {"ImageId": "ami-dedicated"}
    infra.gpu_image("i-source")
    infra.gpu_image("i-source")
    infra.ec2.create_image.assert_called_once()
    args = infra.ec2.create_image.call_args.kwargs
    assert args["NoReboot"] is True and args["InstanceId"] == "i-source"
    assert infra.state["gpu_image"] == "ami-dedicated"


def test_new_gpu_bootstrap_guards_source_and_fetches_fresh_credentials_at_runtime():
    script = bootstrap("arn:aws:secretsmanager:us-east-1:123456789012:secret:new", "i-original")
    assert 'test "$instance" != "i-original"' in script
    assert "get_secret_value" in script
    assert "OPEN_MODELS_API_KEY_FILE" in script
    assert "dmr-decider" in script and "dmr-h2o" in script and "dmr-liquid" in script
    assert "SecretString" in script
    assert "Cognito" not in script and "cloudformation" not in script.lower()
