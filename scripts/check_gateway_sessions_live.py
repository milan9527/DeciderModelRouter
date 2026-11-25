"""Small live conversation/cache checks; outputs contain only synthetic test prompts."""

import argparse
import json
import secrets
import time
from pathlib import Path

import boto3
import httpx

from app.gateway_store import clean


def check_browser(base, credentials, report, output):
    from playwright.sync_api import expect, sync_playwright

    cache = next(c for c in report["cases"] if c["case"] == "cache-followup")
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1050})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        page.locator('input[name="username"]').fill(credentials["username"])
        page.locator('input[name="password"]').fill(credentials["password"])
        page.locator("#login-submit").click()
        expect(page.locator("#app")).to_be_visible()
        expect(page.locator("#use-history")).to_be_checked()
        expect(page.locator("#use-cache")).to_be_checked()
        expect(page.locator("#use-stream")).to_be_checked()
        page.locator("#session-select").select_option(cache["session_id"])
        page.locator('[data-page="performance"]').click()
        page.locator("#history-scope").select_option("session")
        expect(page.locator("#history tbody tr")).to_have_count(2)
        expect(page.locator("#session-metrics")).to_contain_text(str(cache["cache"]["read_tokens"]))
        page.screenshot(path=str(output / "live-performance.png"), full_page=True)
        page.locator(f'[data-run="{cache["run_id"]}"]').click()
        expect(page.locator("#history-detail-content")).to_contain_text("ORBIT-742")
        page.locator("#close-history-detail").click()
        page.locator('[data-page="workspace"]').click()
        page.locator(".conversation-panel summary").click()
        expect(page.locator("#conversation-thread")).to_contain_text("ORBIT-742")
        page.screenshot(path=str(output / "live-conversation.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "live-mobile.png"), full_page=True)
        page.reload()
        expect(page.locator("#session-select")).to_have_value(cache["session_id"])
        page.locator('[data-page="performance"]').click()
        page.locator("#history-scope").select_option("session")
        expect(page.locator("#history tbody tr")).to_have_count(2)
        assert not errors, errors
        browser.close()
    checks = {"browser_errors": errors, "conversation_visible": True, "real_cache_metrics_visible": True,
              "records_after_reload": 2, "mobile_overflow": False, "additional_model_calls": 0}
    (output / "live-browser.json").write_text(json.dumps(checks, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://d3eipg2g3l3an3.cloudfront.net")
    parser.add_argument("--secret", default="DeciderModelRouter/admin-credentials")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser-only", action="store_true", help="Read an existing live report; no model calls")
    args = parser.parse_args()
    if not args.browser_only:
        args.output.mkdir(parents=True, exist_ok=False)
    secret = json.loads(boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId=args.secret)["SecretString"])
    credentials = {k: secret[k] for k in ("username", "password")}
    if args.browser_only:
        report = json.loads((args.output / "verification.json").read_text())
        check_browser(args.base, credentials, report, args.output)
        return
    headers = {"X-Decider-Client": "web"}
    report = {"scope": "live gateway integration; not a model-quality benchmark", "cases": []}

    def save():
        (args.output / "verification.json").write_text(
            json.dumps(clean(report), ensure_ascii=False, indent=2) + "\n")

    with httpx.Client(base_url=args.base, headers=headers, timeout=200, trust_env=False) as client:
        client.post("/api/auth/login", json=credentials).raise_for_status()
        catalog = client.get("/api/gateway/catalog").json()
        assert catalog["session_features"]["multi_turn"]
        versions = {k: catalog[k] for k in ("catalog_version", "ranking_version", "policy_version")}

        def new_session(title):
            result = client.post("/api/gateway/sessions", json={"title": title})
            result.raise_for_status()
            return result.json()["id"]

        def run(case, sid, text, **extra):
            payload = {**versions, "session_id": sid, "provider": "h2o", "mode": "live",
                       "execute_target": True, "client_request_id": secrets.token_hex(16),
                       "task": {"text": text, "output_modality": "text", "routing_preferences": {
                           "benchmark_release": "2025-04-02", "allowed_models": ["amazon.nova-micro-v1:0"],
                           "expected_output_tokens": 128, "max_output_tokens": 512,
                       }}, "session": {"stream": True}, **extra}
            started, trace, arrivals = time.perf_counter(), [], []
            with client.stream("POST", "/api/gateway/runs", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    trace.append(event)
                    if event["type"] == "delta":
                        arrivals.append(round((time.perf_counter() - started) * 1000, 3))
            (args.output / f"{case}.json").write_text(
                json.dumps(clean(trace), ensure_ascii=False, indent=2) + "\n")
            result = next((e["data"] for e in trace if e.get("stage") == "execution"
                           and e.get("status") == "completed"), {})
            selected = next((e["data"] for e in trace if e.get("stage") == "selection"
                             and e.get("status") == "completed"), {})
            record = {"case": case, "run_id": trace[0]["request_id"], "session_id": sid,
                      "terminal": trace[-1], "text": result.get("text"),
                      "context": next((e["data"] for e in trace if e["type"] == "context"), None),
                      "stickiness": selected.get("selection", {}).get("stickiness"),
                      "model_id": result.get("model_id"), "cache": result.get("cache"),
                      "usage": result.get("usage"), "timings": result.get("timings"),
                      "delta_events": len(arrivals), "first_delta_client_ms": next(iter(arrivals), None),
                      "last_delta_client_ms": arrivals[-1] if arrivals else None,
                      "client_total_ms": round((time.perf_counter() - started) * 1000, 3)}
            report["cases"].append(record)
            save()
            print(json.dumps({"case": case, "terminal": trace[-1]["type"], "text": result.get("text"),
                              "cache": result.get("cache"), "delta_events": len(arrivals)}), flush=True)
            assert trace[-1]["type"] == "completed" and result.get("complete"), trace[-1]
            assert arrivals, "No real streamed text events"
            stored = client.get("/api/gateway/history/" + record["run_id"]).json()
            assert stored["events"] == clean(trace)
            assert stored["conversation"][-1]["text"] == result["text"]
            return record

        sid = new_session("验证 · 连续追问与模型粘性")
        first = run("memory-first", sid, "Remember the project code ORBIT-742. Reply only ACK.")
        second = run("memory-followup", sid, "What project code did I give you earlier? Reply only the code.")
        report["memory_pass"] = "ORBIT-742" in second["text"]
        report["history_pass"] = second["context"]["history_turns"] == 1
        report["sticky_observed"] = second["stickiness"]["retained"]
        # A long, fixed synthetic reference makes a genuine cache hit observable.
        reference = "Reference catalog. The project code is ORBIT-742.\n" + "\n".join(
            f"Item {i:03d}: sample record alpha beta gamma delta." for i in range(160))
        cache_sid = new_session("验证 · Bedrock 原生提示词缓存")
        cache_first = run("cache-first", cache_sid, "From the reference catalog, reply only with the project code.",
                          history=[{"role": "user", "text": reference},
                                   {"role": "assistant", "text": "I will use this reference catalog."}],
                          session={"stream": True, "use_history": False})
        cache_second = run("cache-followup", cache_sid, "Repeat that project code. Reply only the code.")
        report["cache_read_observed"] = (cache_second.get("cache") or {}).get("read_tokens", 0) > 0
        report["cache_write_observed"] = (cache_first.get("cache") or {}).get("write_tokens", 0) > 0
        fresh = run("new-session", new_session("验证 · 新会话隔离"), "Reply only NEW.")
        report["new_session_empty"] = fresh["context"]["history_turns"] == 0
        temp = {"username": "session_audit_" + secrets.token_hex(4), "password": secrets.token_urlsafe(24)}
        client.post("/api/gateway/users", json=temp).raise_for_status()
        try:
            with httpx.Client(base_url=args.base, headers=headers, timeout=30) as other:
                other.post("/api/auth/login", json=temp).raise_for_status()
                code = other.get("/api/gateway/history/" + first["run_id"]).status_code
                report["cross_user_history_status"] = code
                assert code == 404
        finally:
            client.post("/api/gateway/users/" + temp["username"], json={"enabled": False}).raise_for_status()
        report["statistics"] = client.get("/api/gateway/performance?session_id=" + cache_sid).json()
        save()
        assert report["memory_pass"] and report["history_pass"] and report["new_session_empty"]
        # Missing cache hits are a reported result, not silently retried or called a pass.
        report["cache_result"] = "hit" if report["cache_read_observed"] else "no_hit_observed"
        save()


if __name__ == "__main__":
    main()
