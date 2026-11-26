import copy
import io
import json
import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from deployment import deploy_gateway as deploy

CONFIG = {
    "region": "us-east-1",
    "cluster": "cluster",
    "service": "service",
    "bucket": "private-assets",
    "distribution_id": "DIST",
}
DIGEST = "sha256:" + "a" * 64
IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/api"


def definition():
    return {
        "taskDefinitionArn": "old-task",
        "family": "api",
        "revision": 3,
        "status": "ACTIVE",
        "taskRoleArn": "task-role",
        "executionRoleArn": "execution-role",
        "networkMode": "awsvpc",
        "cpu": "256",
        "memory": "512",
        "requiresCompatibilities": ["FARGATE"],
        "runtimePlatform": {"cpuArchitecture": "ARM64"},
        "containerDefinitions": [
            {
                "name": "api",
                "image": IMAGE + ":old",
                "readonlyRootFilesystem": True,
                "secrets": [{"name": "DECIDER_API_KEY", "valueFrom": "secret-reference"}],
                "environment": [
                    {"name": "GPU_PROVIDER", "value": "vllm"},
                    {"name": "OPEN_MODELS_URL", "value": "http://private-gpu:18124"},
                    {"name": "STRANDS_DECIDER_URL", "value": "http://private-gpu:8100"},
                    {"name": "AUTH_TABLE", "value": "existing-auth"},
                    {"name": "BEDROCK_ENABLED", "value": "true"},
                ],
            },
            {"name": "sidecar", "image": "untouched", "essential": False},
        ],
        "tags": [{"key": "project", "value": "gateway"}],
    }


ALLOWED = set(definition()) - {"taskDefinitionArn", "revision", "status"}


def service(task="old-task", state="COMPLETED"):
    return {
        "status": "ACTIVE",
        "taskDefinition": task,
        "desiredCount": 1,
        "runningCount": 1,
        "pendingCount": 0,
        "deployments": [{"taskDefinition": task, "rolloutState": state}],
        "deploymentController": {"type": "ECS"},
    }


@pytest.fixture
def publisher():
    clients = {name: MagicMock() for name in ("ecs", "s3", "cloudfront", "ecr")}
    session = SimpleNamespace(client=lambda name, **kw: clients[name])
    pub = deploy.Publisher(CONFIG, session)
    pub.ecs.describe_services.return_value = {"services": [service()]}
    pub.ecs.describe_task_definition.return_value = {"taskDefinition": definition()}
    pub.ecs.register_task_definition.return_value = {
        "taskDefinition": {"taskDefinitionArn": "new-task"}
    }
    pub.ecs.meta.service_model.operation_model.return_value = SimpleNamespace(
        input_shape=SimpleNamespace(members=ALLOWED)
    )
    pub.cf.get_distribution.return_value = {
        "Distribution": {
            "Status": "Deployed",
            "DomainName": "example.cloudfront.net",
            "DistributionConfig": {
                "Enabled": True,
                "Origins": {
                    "Items": [
                        {
                            "Id": "frontend",
                            "DomainName": "private-assets.s3.us-east-1.amazonaws.com",
                            "OriginAccessControlId": "OAC",
                            "S3OriginConfig": {},
                        },
                        {
                            "Id": "api",
                            "DomainName": "private-alb",
                            "VpcOriginConfig": {"VpcOriginId": "vpco"},
                        },
                    ]
                },
                "DefaultCacheBehavior": {"TargetOriginId": "frontend"},
                "CacheBehaviors": {
                    "Items": [
                        {
                            "PathPattern": "/api/*",
                            "TargetOriginId": "api",
                            "CachePolicyId": "disabled",
                        }
                    ]
                },
            },
        }
    }
    pub.cf.get_origin_access_control.return_value = {
        "OriginAccessControl": {
            "OriginAccessControlConfig": {
                "OriginAccessControlOriginType": "s3",
                "SigningBehavior": "always",
                "SigningProtocol": "sigv4",
            }
        }
    }
    pub.cf.get_cache_policy.return_value = {
        "CachePolicy": {
            "CachePolicyConfig": {
                "MinTTL": 0,
                "DefaultTTL": 0,
                "MaxTTL": 0,
            }
        }
    }
    pub.cf.create_invalidation.return_value = {"Invalidation": {"Id": "INV"}}
    pub.s3.get_public_access_block.return_value = {
        "PublicAccessBlockConfiguration": {
            k: True
            for k in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        }
    }
    pub.s3.get_bucket_policy_status.return_value = {"PolicyStatus": {"IsPublic": False}}
    pub.s3.get_bucket_versioning.return_value = {"Status": "Enabled"}
    pub.s3.head_object.return_value = {"VersionId": "previous-version"}
    pub.s3.put_object.return_value = {"VersionId": "new-version"}
    pub.ecr.describe_images.return_value = {"imageDetails": [{"imageDigest": DIGEST}]}
    return pub


def assets(tmp_path):
    for filename in deploy.ASSETS:
        (tmp_path / filename).write_text("content: " + filename)
    return deploy.files(tmp_path)


def test_release_contains_every_local_javascript_import():
    root = deploy.ROOT / "frontend/gateway"
    for source in root.glob("*.js"):
        for module in re.findall(r'from\s+["\']\./([^"\']+)["\']', source.read_text()):
            assert module in deploy.ASSETS, f"{source.name} imports unpublished {module}"


def test_task_clone_preserves_roles_secrets_gpu_and_sidecars():
    old = definition()
    old["tags"].append({"key": "aws:managed-tag", "value": "not-user-managed"})
    before = copy.deepcopy(old)
    new = deploy.task_revision(old, IMAGE + "@" + DIGEST, "api", None, ALLOWED)
    assert old == before
    for key in ("taskRoleArn", "executionRoleArn", "networkMode", "runtimePlatform"):
        assert new[key] == old[key]
    assert new["tags"] == [{"key": "project", "value": "gateway"}]
    assert new["containerDefinitions"][1] == old["containerDefinitions"][1]
    api = new["containerDefinitions"][0]
    assert api["secrets"] == old["containerDefinitions"][0]["secrets"]
    env = {e["name"]: e["value"] for e in api["environment"]}
    assert env["OPEN_MODELS_URL"] == "http://private-gpu:18124"
    assert env["AUTH_TABLE"] == "existing-auth"
    assert env["SERVE_FRONTEND"] == "false" and env["GATEWAY_DECISION_PROVIDER"] == "liquid"
    assert env["GATEWAY_DECIDER_MODEL"] == "strands-decider-2B-hobson-v19-vllm"
    assert not any(k in new for k in ("taskDefinitionArn", "revision", "status"))
    assert api["image"] == IMAGE + "@" + DIGEST


def test_missing_gpu_service_does_not_invent_an_endpoint():
    data = definition()
    data["containerDefinitions"][0]["environment"] = []
    with pytest.raises(ValueError, match="HTTP service"):
        deploy.task_revision(data, IMAGE, "api", "h2o", ALLOWED)


def test_pin_ecr_tag_and_reject_other_region(publisher):
    assert publisher.image(IMAGE + ":release") == IMAGE + "@" + DIGEST
    assert publisher.ecr.describe_images.call_args.kwargs["imageIds"] == [{"imageTag": "release"}]
    with pytest.raises(ValueError, match="deployment region"):
        publisher.image(IMAGE.replace("us-east-1", "us-west-2") + ":release")


def test_preflight_checks_oac_and_api_cache_once(publisher, tmp_path):
    current, _, domain = publisher.preflight(assets(tmp_path))
    assert current["taskDefinition"] == "old-task" and domain == "example.cloudfront.net"
    publisher.cf.get_origin_access_control.assert_called_once()
    publisher.cf.get_cache_policy.assert_called_once()
    publisher.ecs.update_service.assert_not_called()
    publisher.s3.put_object.assert_not_called()


@pytest.mark.parametrize(
    "failure", ["public_s3", "oac", "cache", "wrong_route", "unstable", "unversioned"]
)
def test_wrong_resources_fail_before_mutation(publisher, tmp_path, failure):
    if failure == "public_s3":
        publisher.s3.get_bucket_policy_status.return_value["PolicyStatus"]["IsPublic"] = True
    elif failure == "oac":
        publisher.cf.get_origin_access_control.return_value["OriginAccessControl"][
            "OriginAccessControlConfig"
        ]["SigningBehavior"] = "never"
    elif failure == "cache":
        publisher.cf.get_cache_policy.return_value["CachePolicy"]["CachePolicyConfig"]["MinTTL"] = (
            10
        )
    elif failure == "wrong_route":
        publisher.cf.get_distribution.return_value["Distribution"]["DistributionConfig"][
            "CacheBehaviors"
        ]["Items"].insert(
            0,
            {
                "PathPattern": "/api/gateway/*",
                "TargetOriginId": "frontend",
            },
        )
    elif failure == "unstable":
        publisher.ecs.describe_services.return_value["services"][0]["pendingCount"] = 1
    else:
        publisher.s3.get_bucket_versioning.return_value = {}
    with pytest.raises(ValueError):
        publisher.preflight(assets(tmp_path))
    publisher.ecs.register_task_definition.assert_not_called()
    publisher.s3.put_object.assert_not_called()
    publisher.cf.create_invalidation.assert_not_called()


def test_cli_dry_run_only_reads_existing_resources(publisher, tmp_path, monkeypatch, capsys):
    root = tmp_path / "project"
    folder = root / "frontend/gateway"
    folder.mkdir(parents=True)
    assets(folder)
    resource_file = tmp_path / "resources.json"
    resource_file.write_text(json.dumps(CONFIG))
    monkeypatch.setattr(deploy, "ROOT", root)
    monkeypatch.setattr(deploy, "Publisher", lambda config: publisher)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy_gateway",
            "--resources",
            str(resource_file),
            "--image",
            IMAGE + ":release",
            "--dry-run",
        ],
    )
    deploy.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True and plan["update_ecs"] is True
    assert plan["api_image"].endswith("@" + DIGEST)
    assert "secret-reference" not in json.dumps(plan) and "private-gpu" not in json.dumps(plan)
    publisher.ecs.register_task_definition.assert_not_called()
    publisher.ecs.update_service.assert_not_called()
    publisher.s3.put_object.assert_not_called()
    publisher.cf.create_invalidation.assert_not_called()
    assert not (root / ".deployment").exists()


def test_rollout_rollback_does_not_publish_frontend(publisher, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    data = assets(source)
    publisher.ecs.describe_services.side_effect = [
        {"services": [service()]},  # Optimistic pre-update check.
        {"services": [service()]},  # ECS circuit breaker already rolled back.
    ]
    with pytest.raises(RuntimeError, match="rolled back"):
        publisher.publish(data, definition(), "old-task", tmp_path, "example.cloudfront.net")
    publisher.s3.put_object.assert_not_called()
    record = json.loads((tmp_path / "release.json").read_text())
    assert record["new_task_definition"] == "new-task" and record["phase"] == "ecs_update"
    assert "error" in record


def test_uploads_only_gateway_verifies_versions_and_waits_invalidation(publisher, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    data = assets(source)
    by_key = {a["key"]: a["content"] for a in data}
    publisher.s3.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(by_key[kw["Key"]])}
    publisher.ecs.describe_services.side_effect = [
        {"services": [service()]},
        {"services": [service("new-task")]},
    ]
    result = publisher.publish(data, definition(), "old-task", tmp_path, "example.cloudfront.net")
    assert result["phase"] == "complete"
    assert len(result["objects"]) == len(deploy.ASSETS)
    assert all(row["previous_version"] == "previous-version" for row in result["objects"])
    assert all(row["version"] == "new-version" for row in result["objects"])
    assert [call.kwargs["Key"] for call in publisher.s3.put_object.call_args_list][
        -1
    ] == "gateway/index.html"
    assert all(
        call.kwargs["Key"].startswith("gateway/") for call in publisher.s3.put_object.call_args_list
    )
    assert all(
        call.kwargs["VersionId"] == "new-version" for call in publisher.s3.get_object.call_args_list
    )
    publisher.s3.delete_object.assert_not_called()
    assert publisher.cf.create_invalidation.call_args.kwargs["InvalidationBatch"]["Paths"][
        "Items"
    ] == ["/", "/gateway/*"]
    publisher.cf.get_waiter.return_value.wait.assert_called_once()


def test_verification_failure_stops_and_records_uploaded_version(publisher, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    publisher.s3.get_object.return_value = {"Body": io.BytesIO(b"wrong")}
    with pytest.raises(ValueError, match="S3 verification failed"):
        publisher.publish(assets(source), None, "old-task", tmp_path, "example.cloudfront.net")
    publisher.cf.create_invalidation.assert_not_called()
    record = json.loads((tmp_path / "release.json").read_text())
    assert record["objects"][0]["version"] == "new-version"
    assert record["phase"] == "upload" and record["error"]


def test_distinct_releases_do_not_reuse_cloudfront_invalidation(publisher, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    data = assets(source)
    by_key = {a["key"]: a["content"] for a in data}
    publisher.s3.get_object.side_effect = lambda **kw: {"Body": io.BytesIO(by_key[kw["Key"]])}
    references = []
    for release in ("first", "second"):
        directory = tmp_path / release / "release"
        directory.mkdir(parents=True)
        result = publisher.publish(data, None, "old-task", directory, "example.cloudfront.net")
        reference = publisher.cf.create_invalidation.call_args.kwargs["InvalidationBatch"][
            "CallerReference"
        ]
        saved = json.loads((directory / "release.json").read_text())
        assert reference == result["release_id"] == saved["release_id"]
        references.append(reference)
    assert references[0] != references[1]


def test_access_denied_is_not_treated_as_an_absent_object(publisher, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    publisher.s3.head_object.side_effect = ClientError(
        {"Error": {"Code": "403", "Message": "Denied"}}, "HeadObject"
    )
    with pytest.raises(ClientError):
        publisher.publish(assets(source), None, "old-task", tmp_path, "example.cloudfront.net")
    publisher.s3.put_object.assert_not_called()


def test_local_saved_outputs_and_no_stack_client(tmp_path):
    path = tmp_path / "outputs.json"
    path.write_text(
        json.dumps(
            {
                "region": "us-east-1",
                "outputs": {
                    "ClusterName": "cluster",
                    "ServiceName": "service",
                    "FrontendBucketName": "private-assets",
                    "DistributionId": "DIST",
                },
            }
        )
    )
    assert deploy.resources(path) == CONFIG
    session = MagicMock()
    deploy.Publisher(CONFIG, session)
    assert [call.args[0] for call in session.client.call_args_list] == [
        "ecs",
        "s3",
        "cloudfront",
        "ecr",
    ]
