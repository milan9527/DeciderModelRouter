"""Evaluate all three GPU classifiers, verify isolation, and replace the dedicated ECS task."""

import argparse
import json
import secrets
import time
from pathlib import Path

import boto3
import httpx
from playwright.sync_api import expect, sync_playwright


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--resources", type=Path, required=True)
    p.add_argument("--gpu", type=Path, required=True)
    p.add_argument("--credentials", type=Path, required=True)
    p.add_argument("--smoke-report", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    resources = json.loads(args.resources.read_text())
    gpu = json.loads(args.gpu.read_text())
    credentials = json.loads(args.credentials.read_text())
    smoke = json.loads(args.smoke_report.read_text())
    assert resources["cluster"] == resources["project"] == "decider-model-router"
    assert gpu["instance_id"] != "i-0ae7eb0abcb815af3"
    base = "https://" + resources["distribution"]["DomainName"]
    session = boto3.Session(region_name=resources["region"])
    ecs, ec2 = session.client("ecs"), session.client("ec2")
    cluster = resources["cluster"]

    def save(name, value):
        (args.output / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
        )

    def service(name=cluster):
        return ecs.describe_services(cluster=name, services=[name])["services"][0]

    def running():
        return ecs.list_tasks(cluster=cluster, serviceName=cluster, desiredStatus="RUNNING")[
            "taskArns"
        ]

    def instance(iid):
        row = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
        return {
            "id": iid, "state": row["State"]["Name"], "type": row["InstanceType"],
            "vpc": row["VpcId"], "private_ip": row["PrivateIpAddress"],
            "public_ip": row.get("PublicIpAddress"), "launch_time": str(row["LaunchTime"]),
        }

    old_before = service("strands-decider-demo")["taskDefinition"]
    old_gpu = instance("i-0ae7eb0abcb815af3")
    headers = {"X-Decider-Client": "web", "Origin": base}
    with httpx.Client(base_url=base, headers=headers, timeout=180, trust_env=False) as client:
        client.post("/api/auth/login", json=credentials).raise_for_status()
        assert client.get("/api/gateway/account").json()["role"] == "admin"
        cases = client.get("/api/gateway/evaluations/cases").json()
        assert len(cases["items"]) == 16
        body = {
            "providers": ["decider", "liquid", "h2o"],
            "case_ids": [c["id"] for c in cases["items"]],
        }
        response = client.post("/api/gateway/evaluations", json=body)
        response.raise_for_status()
        job = response.json()
        save("evaluation-start.json", job)
        print("Submitted 16 tasks × 3 classifiers; no Bedrock target calls.", flush=True)
        deadline = time.monotonic() + 1200
        while job["status"] == "running" and time.monotonic() < deadline:
            time.sleep(5)
            response = client.get("/api/gateway/evaluations/" + job["id"])
            response.raise_for_status()
            job = response.json()
            save("evaluation.json", job)
            print(f"Evaluation {len(job['results'])}/48: {job['status']}", flush=True)
        assert job["status"] == "completed", job["status"]
        assert len(job["results"]) == job["started_gpu_calls"] == 48
        assert job["target_model_calls"] == 0
        # Mismatches and model failures are results, never rewritten as a passing accuracy.
        ids = smoke["persistence"]["saved_run_ids"]
        before = {}
        for rid in ids:
            response = client.get("/api/gateway/history/" + rid)
            response.raise_for_status()
            before[rid] = response.json()
        reviewer = {"username": "audit_" + secrets.token_hex(4), "password": secrets.token_urlsafe(24)}
        client.post("/api/gateway/users", json=reviewer).raise_for_status()
        try:
            with httpx.Client(base_url=base, headers=headers, timeout=30, trust_env=False) as other:
                other.post("/api/auth/login", json=reviewer).raise_for_status()
                assert other.get("/api/gateway/history").json()["items"] == []
                assert other.get("/api/gateway/users").status_code == 403
                assert other.get("/api/gateway/history/" + ids[0]).status_code == 404
                assert other.get("/api/gateway/evaluations/" + job["id"]).status_code == 404
                client.post("/api/gateway/users/" + reviewer["username"], json={"enabled": False}).raise_for_status()
                assert other.get("/api/gateway/account").status_code == 401
        finally:
            client.post("/api/gateway/users/" + reviewer["username"], json={"enabled": False}).raise_for_status()
        save("isolation.json", {
            "cross_user_history": 404, "cross_user_evaluation": 404, "user_admin_api": 403,
            "disabled_session": 401, "temporary_account": reviewer["username"], "disabled": True,
        })
        previous_tasks = running()
        deployment = ecs.update_service(cluster=cluster, service=cluster, forceNewDeployment=True)[
            "service"
        ]
        expected = next(d["id"] for d in deployment["deployments"] if d["status"] == "PRIMARY")
        print("Replacing the dedicated ECS task to verify database persistence...", flush=True)
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            current = service()
            deployments = current["deployments"]
            tasks = running()
            if (
                len(deployments) == 1
                and deployments[0]["id"] == expected
                and deployments[0].get("rolloutState") == "COMPLETED"
                and tasks and not set(tasks) & set(previous_tasks)
                and current["runningCount"] == current["desiredCount"] == 1
            ):
                break
            if any(d["id"] == expected and d.get("rolloutState") == "FAILED" for d in deployments):
                raise RuntimeError("Independent ECS replacement failed")
            time.sleep(10)
        else:
            raise RuntimeError("Independent ECS replacement did not converge")
        # Keep the existing cookie: authentication must survive as well as business records.
        assert client.get("/api/gateway/account").status_code == 200
        for rid, value in before.items():
            assert client.get("/api/gateway/history/" + rid).json() == value
        assert client.get("/api/gateway/evaluations/" + job["id"]).json() == job
        details = ecs.describe_tasks(cluster=cluster, tasks=tasks)["tasks"]
        save("restart-persistence.json", {
            "previous_tasks": previous_tasks, "new_tasks": tasks, "deployment_id": expected,
            "task_definition": current["taskDefinition"],
            "image_digests": [c.get("imageDigest") for t in details for c in t["containers"]],
            "run_ids": ids, "evaluation_id": job["id"],
            "identical_records_after_restart": True, "login_survived": True,
        })
        client.post("/api/auth/logout", json={}).raise_for_status()
    assert service("strands-decider-demo")["taskDefinition"] == old_before
    assert instance("i-0ae7eb0abcb815af3") == old_gpu
    current_gpu = instance(gpu["instance_id"])
    assert current_gpu["vpc"] == resources["vpc"] and current_gpu["public_ip"] is None
    tables = {}
    ddb = session.client("dynamodb")
    for key in ("auth_table", "data_table"):
        name = resources[key]
        ttl = ddb.describe_time_to_live(TableName=name)["TimeToLiveDescription"]
        recovery = ddb.describe_continuous_backups(TableName=name)["ContinuousBackupsDescription"]
        assert ttl["TimeToLiveStatus"] == "ENABLED"
        assert recovery["PointInTimeRecoveryDescription"]["PointInTimeRecoveryStatus"] == "ENABLED"
        tables[name] = {"ttl": ttl, "recovery": recovery}
    anonymous_s3 = httpx.get(
        f"https://{resources['bucket']}.s3.us-east-1.amazonaws.com/gateway/index.html",
        trust_env=False, timeout=30,
    ).status_code
    assert anonymous_s3 == 403
    save("aws-isolation.json", {
        "cloudfront": resources["distribution"]["Id"], "url": base,
        "s3": resources["bucket"], "oac": resources["oac"], "anonymous_s3": anonymous_s3,
        "vpc": resources["vpc"], "gpu": current_gpu, "tables": tables,
        "old_service_task_definition_unchanged": old_before, "old_gpu_unchanged": old_gpu,
    })
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/gateway/index.html")
        page.locator('input[name="username"]').fill(credentials["username"])
        page.locator('input[name="password"]').fill(credentials["password"])
        page.locator("#login-submit").click()
        expect(page.locator("#app")).to_be_visible(timeout=30000)
        page.locator('[data-page="evaluations"]').click()
        page.locator(f'[data-evaluation="{job["id"]}"]').click()
        expect(page.locator("#evaluation-result h2")).to_have_text("评测结果 · 已完成")
        page.screenshot(path=str(args.output / "real-evaluation.png"), full_page=True)
        page.locator('[data-page="performance"]').click()
        expect(page.locator(f'[data-run="{ids[0]}"]')).to_be_visible()
        page.screenshot(path=str(args.output / "history-after-restart.png"), full_page=True)
        page.locator('[data-page="account"]').click()
        page.screenshot(path=str(args.output / "accounts.png"), full_page=True)
        page.locator("#logout").click()
        expect(page.locator("#auth")).to_be_visible()
        browser.close()
    assert not errors, errors
    report = {
        "status": "passed", "fixture": False, "url": base, "gpu_calls": 48,
        "bedrock_target_calls": 0, "evaluation_id": job["id"],
        "dataset_scope": job["dataset_scope"], "summary": job["summary"],
        "ecs_restart_persistence": True, "account_isolation": True, "browser_errors": errors,
    }
    save("verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
