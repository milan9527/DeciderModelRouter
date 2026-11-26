"""Publish the gateway through ECS, ECR, S3 and CloudFront SDKs, without stack APIs."""

import argparse
import copy
import fnmatch
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("gateway.css", "workspace.js", "agents.js", "gateway.js", "mark.svg", "index.html")
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def resources(path, region=None):
    """Accept explicit resource IDs or a saved local deployment record, never query a stack."""
    record = json.loads(Path(path).read_text())
    values = record.get("outputs", record)
    names = {
        "cluster": "ClusterName",
        "service": "ServiceName",
        "bucket": "FrontendBucketName",
        "distribution_id": "DistributionId",
    }
    result = {key: values.get(key) or values.get(alias) for key, alias in names.items()}
    result["region"] = region or record.get("region", "us-east-1")
    if any(not isinstance(value, str) or not value for value in result.values()):
        raise ValueError(
            "Resource file must specify region, cluster, service, bucket and distribution"
        )
    return result


def files(frontend):
    result = []
    for name in ASSETS:
        path = frontend / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing regular gateway asset: {name}")
        content = path.read_bytes()
        result.append(
            {
                "key": "gateway/" + name,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "content": content,
                "content_type": MIME[path.suffix],
                # Unversioned URLs: require freshness for all assets, not just the HTML.
                "cache_control": "no-cache, max-age=0, must-revalidate",
            }
        )
    return result


def behavior_for(config, path):
    for behavior in config.get("CacheBehaviors", {}).get("Items", []):
        if fnmatch.fnmatchcase(path, "/" + behavior["PathPattern"].lstrip("/")):
            return behavior
    return config["DefaultCacheBehavior"]


def task_revision(definition, image, container, provider, allowed_keys, decision_mode=None):
    result = {key: copy.deepcopy(value) for key, value in definition.items() if key in allowed_keys}
    # AWS-owned tags cannot be supplied to RegisterTaskDefinition by callers.
    if "tags" in result:
        result["tags"] = [
            tag for tag in result["tags"] if not tag["key"].lower().startswith("aws:")
        ]
    matches = [c for c in result["containerDefinitions"] if c["name"] == container]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one container named {container}")
    api = matches[0]
    env = {e["name"]: e["value"] for e in api.get("environment", [])}
    chosen = (
        provider
        or env.get("GATEWAY_DECISION_PROVIDER")
        or "liquid"
    )
    if chosen not in ("decider", "liquid", "h2o"):
        raise ValueError("Only the configured GPU decision services may classify requests")
    url = (
        env.get("OPEN_MODELS_URL", "")
        if chosen != "decider"
        else env.get("GATEWAY_DECIDER_URL") or env.get("STRANDS_DECIDER_URL", "")
    )
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"The existing task has no independent HTTP service for {chosen}")
    updates = {
        "SERVE_FRONTEND": "false",
        "GATEWAY_DECISION_PROVIDER": chosen,
        "GATEWAY_DECIDER_MODEL": env.get("GATEWAY_DECIDER_MODEL")
        or (
            "strands-decider-2B-hobson-v19-vllm"
            if env.get("GPU_PROVIDER") == "vllm"
            else env.get("DECIDER_MODEL", "strands-decider-2B-hobson-v19")
        ),
    }
    if decision_mode is not None:
        if decision_mode not in {"two_stage", "joint"}:
            raise ValueError("Decision mode must be two_stage or joint")
        updates["GATEWAY_DECISION_MODE"] = decision_mode
    api["environment"] = [e for e in api.get("environment", []) if e["name"] not in updates] + [
        {"name": key, "value": value} for key, value in updates.items()
    ]
    api["image"] = image
    return result


class Publisher:
    def __init__(self, config, session=None):
        self.config = config
        self.entry_path = "/gateway/index.html"
        session = session or boto3.Session(region_name=config["region"])
        options = Config(
            connect_timeout=5, read_timeout=15, retries={"mode": "standard", "max_attempts": 2}
        )
        # This is the complete list of AWS services used by this publisher.
        self.ecs, self.s3, self.cf, self.ecr = (
            session.client(service, config=options)
            for service in ("ecs", "s3", "cloudfront", "ecr")
        )

    def service(self):
        response = self.ecs.describe_services(
            cluster=self.config["cluster"], services=[self.config["service"]]
        )
        if response.get("failures") or len(response.get("services", [])) != 1:
            raise ValueError("ECS service not found")
        return response["services"][0]

    def image(self, reference):
        match = re.fullmatch(
            r"(\d{12})\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com/([a-z0-9/_.-]+)"
            r"(?::([A-Za-z0-9_.-]+)|@(sha256:[a-f0-9]{64}))",
            reference,
        )
        if not match or match[2] != self.config["region"]:
            raise ValueError(
                "Use a tagged or digest-pinned private ECR image in the deployment region"
            )
        identity = {"imageTag": match[4]} if match[4] else {"imageDigest": match[5]}
        rows = self.ecr.describe_images(
            registryId=match[1], repositoryName=match[3], imageIds=[identity]
        )["imageDetails"]
        if len(rows) != 1:
            raise ValueError("ECR image did not resolve to one digest")
        return f"{match[1]}.dkr.ecr.{match[2]}.amazonaws.com/{match[3]}@{rows[0]['imageDigest']}"

    def preflight(self, assets):
        cfg = self.config
        service = self.service()
        if (
            service.get("status") != "ACTIVE"
            or service.get("deploymentController", {}).get("type", "ECS") != "ECS"
            or len(service.get("deployments", [])) != 1
            or service["deployments"][0].get("rolloutState") != "COMPLETED"
            or service.get("desiredCount", 0) < 1
            or service.get("runningCount") != service["desiredCount"]
            or service.get("pendingCount", 0) != 0
        ):
            raise ValueError("ECS must have a healthy, stable rolling service before publishing")
        distribution = self.cf.get_distribution(Id=cfg["distribution_id"])["Distribution"]
        dist = distribution["DistributionConfig"]
        if distribution["Status"] != "Deployed" or not dist["Enabled"]:
            raise ValueError("CloudFront must be enabled and fully deployed")
        self.entry_path = (
            "/"
            if dist.get("DefaultRootObject") == "gateway/index.html"
            and dist["DefaultCacheBehavior"]["TargetOriginId"]
            == behavior_for(dist, "/gateway/index.html")["TargetOriginId"]
            else "/gateway/index.html"
        )
        origins = {row["Id"]: row for row in dist["Origins"]["Items"]}
        oacs, policies = {}, {}
        for asset in assets:
            behavior = behavior_for(dist, "/" + asset["key"])
            origin = origins[behavior["TargetOriginId"]]
            if not (
                origin["DomainName"].startswith(cfg["bucket"] + ".s3")
                and origin["DomainName"].endswith(".amazonaws.com")
                and origin.get("OriginAccessControlId")
                and "S3OriginConfig" in origin
            ):
                raise ValueError(
                    "Gateway assets must use the configured private S3 origin with OAC"
                )
            oid = origin["OriginAccessControlId"]
            if oid not in oacs:
                oacs[oid] = self.cf.get_origin_access_control(Id=oid)["OriginAccessControl"][
                    "OriginAccessControlConfig"
                ]
            oac = oacs[oid]
            if (
                oac["OriginAccessControlOriginType"] != "s3"
                or oac["SigningBehavior"] != "always"
                or oac["SigningProtocol"] != "sigv4"
            ):
                raise ValueError("The S3 OAC must always sign with SigV4")
        for path in ("/api/gateway/runs", "/api/gateway/catalog", "/api/auth/login"):
            api = behavior_for(dist, path)
            if not origins[api["TargetOriginId"]].get("VpcOriginConfig"):
                raise ValueError("Gateway API must use the existing private VPC origin")
            pid = api["CachePolicyId"]
            if pid not in policies:
                policies[pid] = self.cf.get_cache_policy(Id=pid)["CachePolicy"]["CachePolicyConfig"]
            policy = policies[pid]
            if any(policy[k] != 0 for k in ("MinTTL", "DefaultTTL", "MaxTTL")):
                raise ValueError("Authenticated gateway API responses must not be cached")
        block = self.s3.get_public_access_block(Bucket=cfg["bucket"])[
            "PublicAccessBlockConfiguration"
        ]
        if not all(
            block.get(k) is True
            for k in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        ):
            raise ValueError("S3 public access must remain fully blocked")
        if self.s3.get_bucket_policy_status(Bucket=cfg["bucket"])["PolicyStatus"]["IsPublic"]:
            raise ValueError("The frontend bucket policy must be private")
        if self.s3.get_bucket_versioning(Bucket=cfg["bucket"]).get("Status") != "Enabled":
            raise ValueError(
                "The existing frontend bucket must have versioning for recoverable releases"
            )
        definition = self.ecs.describe_task_definition(
            taskDefinition=service["taskDefinition"], include=["TAGS"]
        )
        return service, definition, distribution["DomainName"]

    def publish(self, assets, definition, previous, directory, domain):
        cfg = self.config
        record = {
            "release_id": uuid4().hex,
            "resources": cfg,
            "previous_task_definition": previous,
            "new_task_definition": None,
            "phase": "prepared",
            "objects": [],
            "url": f"https://{domain}{self.entry_path}",
        }

        def save():
            path = directory / "release.json"
            path.write_text(json.dumps(record, indent=2) + "\n")
            path.chmod(0o600)

        save()
        try:
            # Detect a second deployment after preflight rather than overwrite its task revision.
            if self.service()["taskDefinition"] != previous:
                raise ValueError(
                    "ECS task changed after preflight; rerun against the current service"
                )
            if definition:
                new = self.ecs.register_task_definition(**definition)["taskDefinition"][
                    "taskDefinitionArn"
                ]
                record.update(new_task_definition=new, phase="ecs_update")
                save()
                self.ecs.update_service(
                    cluster=cfg["cluster"], service=cfg["service"], taskDefinition=new
                )
                self.wait_service(new)
            record["phase"] = "upload"
            save()
            for asset in assets:
                key = asset["key"]
                try:
                    old = self.s3.head_object(Bucket=cfg["bucket"], Key=key)
                    version = old["VersionId"]
                except ClientError as exc:
                    if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                        raise
                    version = None
                entry = {k: asset[k] for k in ("key", "sha256", "size")}
                entry["previous_version"] = version
                record["objects"].append(entry)
                save()
                uploaded = self.s3.put_object(
                    Bucket=cfg["bucket"],
                    Key=key,
                    Body=asset["content"],
                    ContentType=asset["content_type"],
                    CacheControl=asset["cache_control"],
                    Metadata={"sha256": asset["sha256"]},
                )
                entry["version"] = uploaded["VersionId"]
                save()
                # Verify stored bytes of precisely this version, not an overlapping release.
                response = self.s3.get_object(
                    Bucket=cfg["bucket"], Key=key, VersionId=entry["version"]
                )
                stream = response["Body"]
                try:
                    if hashlib.sha256(stream.read()).hexdigest() != asset["sha256"]:
                        raise ValueError(f"S3 verification failed: {key}")
                finally:
                    stream.close()
            record["phase"] = "invalidation"
            save()
            result = self.cf.create_invalidation(
                DistributionId=cfg["distribution_id"],
                InvalidationBatch={
                    # Output folders often share the basename "release". CloudFront
                    # reuses a previous invalidation for the same caller reference.
                    "CallerReference": record["release_id"],
                    # CloudFront also caches the default root object under "/".
                    "Paths": {"Quantity": 2, "Items": ["/", "/gateway/*"]},
                },
            )
            record["invalidation_id"] = result["Invalidation"]["Id"]
            save()
            self.cf.get_waiter("invalidation_completed").wait(
                DistributionId=cfg["distribution_id"],
                Id=record["invalidation_id"],
                WaiterConfig={"Delay": 10, "MaxAttempts": 120},
            )
            record["phase"] = "complete"
            save()
            return record
        except Exception as exc:
            record["error"] = str(exc)
            save()
            raise

    def wait_service(self, expected, timeout=1200):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.service()
            if current["taskDefinition"] != expected:
                raise RuntimeError("ECS rolled back or another release replaced the expected task")
            active = [d for d in current["deployments"] if d["taskDefinition"] == expected]
            if any(d.get("rolloutState") == "FAILED" for d in active):
                raise RuntimeError("ECS rollout failed; frontend has not been uploaded")
            if (
                len(current["deployments"]) == 1
                and len(active) == 1
                and active[0].get("rolloutState") == "COMPLETED"
                and current["runningCount"] == current["desiredCount"]
                and current.get("pendingCount", 0) == 0
            ):
                return
            print("Waiting for the expected ECS task revision...", flush=True)
            time.sleep(10)
        raise TimeoutError("ECS did not stabilize; frontend has not been uploaded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, default=ROOT / ".deployment/outputs.json")
    parser.add_argument("--region")
    release = parser.add_mutually_exclusive_group(required=True)
    release.add_argument("--image", help="Existing ECR API image URI:tag or URI@sha256:digest")
    release.add_argument(
        "--frontend-only", action="store_true", help="Use an already deployed gateway API"
    )
    parser.add_argument("--container", default="api")
    parser.add_argument("--provider", choices=("decider", "h2o", "liquid"))
    parser.add_argument("--decision-mode", choices=("two_stage", "joint"))
    parser.add_argument(
        "--dry-run", action="store_true", help="Read AWS state and print plan; no writes"
    )
    parser.add_argument("--output", type=Path, help="New local release directory")
    args = parser.parse_args()
    if args.frontend_only and (args.provider or args.decision_mode):
        parser.error("--provider / --decision-mode changes the API task; use --image")
    cfg = resources(args.resources, args.region)
    assets = files(ROOT / "frontend/gateway")
    publisher = Publisher(cfg)
    service, current, domain = publisher.preflight(assets)
    definition = None
    if args.image:
        allowed = publisher.ecs.meta.service_model.operation_model(
            "RegisterTaskDefinition"
        ).input_shape.members
        definition = task_revision(
            {**current["taskDefinition"], "tags": current.get("tags", [])},
            publisher.image(args.image),
            args.container,
            args.provider,
            allowed,
            args.decision_mode,
        )
    image = next(
        c["image"]
        for c in (definition or current["taskDefinition"])["containerDefinitions"]
        if c["name"] == args.container
    )
    plan = {
        "resources": cfg,
        "previous_task_definition": service["taskDefinition"],
        "api_image": image,
        "update_ecs": definition is not None,
        "gateway_environment": {
            e["name"]: e["value"]
            for c in (definition or current["taskDefinition"])["containerDefinitions"]
            if c["name"] == args.container
            for e in c.get("environment", [])
            if e["name"] in ("SERVE_FRONTEND", "GATEWAY_DECISION_PROVIDER", "GATEWAY_DECIDER_MODEL", "GATEWAY_DECISION_MODE")
        },
        "frontend": [{k: a[k] for k in ("key", "size", "sha256")} for a in assets],
        "invalidation_paths": ["/", "/gateway/*"],
        "url": f"https://{domain}{publisher.entry_path}",
        "dry_run": args.dry_run,
    }
    print(json.dumps(plan, indent=2), flush=True)
    if args.dry_run:
        return
    directory = args.output or ROOT / ".deployment/gateway-releases" / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    (directory / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    # Keep a local recovery definition, preserving IAM roles, secrets references and sidecars.
    (directory / "previous-task.json").write_text(json.dumps(current, indent=2, default=str) + "\n")
    print(f"Release record: {directory}", flush=True)
    result = publisher.publish(assets, definition, service["taskDefinition"], directory, domain)
    print(json.dumps({"phase": result["phase"], "url": result["url"]}), flush=True)


if __name__ == "__main__":
    main()
