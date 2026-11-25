"""Verify the deployed gateway with one GPU route and one browser GPU + Bedrock execution."""

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def redact(value):
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items() if k != "route_token"}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", choices=("smoke", "code"), default="smoke")
    parser.add_argument("--benchmark-release", default="auto")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    base = args.url.rstrip("/")
    credentials = json.loads(args.credentials.read_text())
    report = {
        "url": base,
        "scenario": args.scenario,
        "fixture": False,
        "http_route": None,
        "browser": None,
    }

    def save(name, value):
        (args.output / name).write_text(
            json.dumps(redact(value), ensure_ascii=False, indent=2) + "\n"
        )

    with httpx.Client(
        base_url=base, headers={"X-Decider-Client": "web"}, timeout=180, trust_env=False
    ) as client:
        assert client.get("/api/gateway/catalog").status_code == 401
        client.post("/api/auth/login", json=credentials).raise_for_status()
        catalog = client.get("/api/gateway/catalog")
        catalog.raise_for_status()
        catalog = catalog.json()
        persistent = catalog.get("persistent_history", False)
        baseline = client.get("/api/gateway/performance").json() if persistent else {}
        expected_live = baseline.get("live_requests", 0) + 2 if persistent else 1
        status = client.get("/api/gateway/status").json()
        save("status.json", status)
        assert status["decision"]["connected"], status
        default_provider = catalog["decision_service"]["default_provider"]
        default_model = next(
            p["model"] for p in catalog["decision_service"]["providers"]
            if p["id"] == default_provider
        )
        assert status["decision"]["provider"] == default_provider
        checksums = {}
        for asset in (ROOT / "frontend/gateway").iterdir():
            if asset.is_file():
                response = client.get("/gateway/" + asset.name)
                response.raise_for_status()
                assert response.content == asset.read_bytes(), asset.name
                checksums[asset.name] = hashlib.sha256(response.content).hexdigest()
        report["assets"] = checksums
        code_scenario = args.scenario == "code"
        code_example = next(row for row in catalog["examples"] if row["id"] == "code")
        task_text = code_example["task"] if code_scenario else "请用一句中文解释支付接口的幂等性。"
        prefs = {
            "cost_tier": "medium" if code_scenario else "low",
            "benchmark_release": args.benchmark_release,
            **(
                code_example["routing_preferences"]
                if code_scenario
                else {"expected_output_tokens": 128, "max_output_tokens": 1024}
            ),
        }
        blocked = {
            m["id"]
            for m in catalog["models"]
            if any(q["value"] == 0 for q in m.get("inference_quotas", {}).get("checks", []))
        }
        if code_scenario:
            assert {"moonshotai.kimi-k3", "zai.glm-5.3"} <= blocked
        payload = {
            "task": {"text": task_text, "routing_preferences": prefs},
            "mode": "live",
            "execute_target": False,
            **{k: catalog[k] for k in ("catalog_version", "ranking_version", "policy_version")},
        }
        if persistent:
            session = client.post("/api/gateway/sessions", json={"title": "上线验收 · 真实模型路由"})
            session.raise_for_status()
            payload.update(session_id=session.json()["id"], client_request_id=str(uuid4()))
        received, started = [], perf_counter()
        with client.stream("POST", "/api/gateway/runs", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.strip():
                    received.append(
                        {
                            "client_received_ms": round((perf_counter() - started) * 1000, 3),
                            "event": json.loads(line),
                        }
                    )
        save("http-route.json", {"request": payload, "received": received})
        events = [row["event"] for row in received]
        assert events[-1]["type"] == "completed", events[-1]
        assert events[-1]["data"]["status"] == "selected", events[-1]
        route = next(e["data"] for e in events if e["type"] == "route")
        profile, selected = route["steps"]
        assert profile["source"] == "live" and profile["assessment_count"] == 7
        assert profile["model"] == default_model
        assert selected["selected_model"]["id"] in {m["id"] for m in catalog["models"]}
        assert selected["selected_model"]["id"] not in blocked
        for mid in blocked:
            assert "配额为 0" in selected["selection"]["excluded"][mid]
        report["zero_quota_exclusions"] = sorted(blocked)
        report["http_route"] = {
            "request_id": events[0]["request_id"],
            "decision_model": profile["model"],
            "selected_model": selected["selected_model"]["id"],
            "gpu_round_trip_ms": profile["timings"]["upstream_ms"],
            "route_ms": route["route_total_ms"],
            "benchmark_release": selected["selection"]["benchmark_group"]["release"],
        }
        if args.benchmark_release != "auto":
            assert selected["selection"]["benchmark_group"]["release"] == args.benchmark_release
        report["mapping_coverage"] = {
            k: catalog["evidence_inventory"][k] for k in ("mapped_models", "total_models", "counts")
        }
        if persistent:
            duplicate = client.post("/api/gateway/runs", json=payload)
            assert duplicate.status_code == 409
            assert events[0]["request_id"] in duplicate.json()["detail"]
            report["duplicate_request_rejected"] = duplicate.status_code
        client.post("/api/auth/logout", json={}).raise_for_status()

    errors, urls = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: urls.append(request.url))
        page.goto(base + "/gateway/index.html")
        page.locator('input[name="username"]').fill(credentials["username"])
        page.locator('input[name="password"]').fill(credentials["password"])
        page.locator("#login-submit").click()
        expect(page.locator("#app")).to_be_visible(timeout=30000)
        expect(page.locator("#health")).to_have_class("status ready", timeout=30000)
        expect(page.locator("#provider")).to_have_value(default_provider)
        if code_scenario:
            expect(page.locator("#task")).to_have_value(task_text)
            expect(page.locator("#max-output")).to_have_value(str(prefs["max_output_tokens"]))
            expect(page.locator("#expected-output")).to_have_value(
                str(prefs["expected_output_tokens"])
            )
        page.locator("#task").fill(task_text)
        page.locator(f'input[name="tier"][value="{prefs["cost_tier"]}"]').check()
        page.locator(".advanced summary").click()
        page.locator("#benchmark-release").select_option(args.benchmark_release)
        page.locator("#expected-output").fill(str(prefs["expected_output_tokens"]))
        page.locator("#max-output").fill(str(prefs["max_output_tokens"]))
        page.locator("#max-cost").fill("" if code_scenario else "0.02")
        page.locator(".advanced summary").click()
        page.locator("#run").click()
        expect(page.locator("#run")).to_be_enabled(timeout=180000)
        with page.expect_download() as download:
            page.locator("#export").click()
        exported = json.loads(Path(download.value.path()).read_text())
        save("browser-run.json", exported)
        # Save the UI even on failure to make upstream issues diagnosable.
        page.evaluate("scrollTo(0, 0)")
        page.screenshot(path=str(args.output / "desktop.png"), full_page=True)
        expect(page.locator("#run-badge")).to_have_text("已完成")
        expect(page.locator(".profile-chips span")).to_have_count(7)
        expect(page.locator("#stages .completed")).to_have_count(4)
        expect(page.locator(".output")).not_to_be_empty()
        result = next(
            e["data"]
            for e in exported["events"]
            if e.get("stage") == "execution" and e.get("status") == "completed"
        )
        assert result["source"] == "live" and result["complete"]
        assert result["model_id"] in {m["id"] for m in catalog["models"]}
        assert result["model_id"] not in blocked
        chosen = next(
            e["data"] for e in exported["events"]
            if e.get("stage") == "selection" and e.get("status") == "completed"
        )
        if args.benchmark_release != "auto":
            assert chosen["selection"]["benchmark_group"]["release"] == args.benchmark_release
        report["browser"] = {
            "request_id": exported["events"][0]["request_id"],
            "model_id": result["model_id"],
            "target_ms": result["backend_total_ms"],
            "usage": result["usage"],
            "observed_reference_cost_usd": result["observed_reference_cost_usd"],
            "total_ms": exported["events"][-1]["data"]["backend_total_ms"],
        }
        page.locator('[data-tab="candidates"]').click()
        expect(page.locator(".candidate-table .selected")).to_have_count(1)
        if blocked:
            page.locator("#candidates .excluded summary").click()
            expect(page.locator("#candidates .excluded")).to_contain_text("账户推理配额为 0")
            page.screenshot(path=str(args.output / "candidates.png"), full_page=True)
        page.locator('[data-tab="timing"]').click()
        page.evaluate("scrollTo(0, 0)")
        page.screenshot(path=str(args.output / "timings.png"), full_page=True)
        page.locator('[data-page="performance"]').click()
        expect(page.locator("#session-metrics .metric-value").first).to_have_text(str(expected_live))
        if persistent:
            saved = []
            for run_id in (report["http_route"]["request_id"], report["browser"]["request_id"]):
                response = page.request.get(base + "/api/gateway/history/" + run_id)
                assert response.ok
                record = response.json()
                assert record["events"] and record["id"] == run_id
                assert "route_token" not in json.dumps(record)
                saved.append(record)
            save("persisted-runs.json", saved)
            page.reload()
            expect(page.locator("#app")).to_be_visible(timeout=30000)
            page.locator('[data-page="performance"]').click()
            expect(page.locator("#session-metrics .metric-value").first).to_have_text(str(expected_live))
            for record in saved:
                expect(page.locator(f'[data-run="{record["id"]}"]')).to_be_visible()
            page.screenshot(path=str(args.output / "persistent-history.png"), full_page=True)
            report["persistence"] = {
                "backend": "DynamoDB",
                "baseline_live_requests": baseline["live_requests"],
                "live_requests": expected_live,
                "saved_run_ids": [r["id"] for r in saved],
                "survives_browser_reload": True,
                "route_tokens_removed": True,
            }
        if blocked:
            page.locator('[data-page="models"]').click()
            page.locator("#model-table .excluded summary").click()
            expect(page.locator("#model-table .excluded")).to_contain_text("Kimi K3")
            page.screenshot(path=str(args.output / "quota-models.png"), full_page=True)
        page.locator('[data-page="models"]').click()
        page.locator("#category").select_option("customer_support")
        page.locator("#model-search").fill("Nova Micro")
        nova = page.locator('[data-model-id="amazon.nova-micro-v1:0"]')
        expect(nova).to_contain_text("已映射评测")
        nova.locator(".model-evidence summary").click()
        expect(nova).to_contain_text("2025-04-02")
        page.screenshot(path=str(args.output / "mapping-nova.png"), full_page=True)
        page.locator("#model-search").fill("qwen3-coder-next")
        expect(page.locator("#model-table")).to_contain_text("型号资料已核验")
        page.locator("#model-search").fill("")
        page.locator('[data-page="workspace"]').click()
        page.locator('[data-tab="response"]').click()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate("scrollTo(0, 0)")
        page.screenshot(path=str(args.output / "mobile.png"), full_page=True)
        assert not errors, errors
        assert all(url.startswith(base + "/") for url in urls), urls
        page.locator("#logout").click()
        expect(page.locator("#auth")).to_be_visible()
        browser.close()
    report.update(browser_errors=errors, gpu_calls=2, bedrock_target_calls=1, status="passed")
    save("verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
