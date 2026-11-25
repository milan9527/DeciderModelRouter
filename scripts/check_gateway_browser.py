"""Three-process gateway smoke test. All model responses are synthetic test fixtures."""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(role, port):
    import uvicorn

    if role == "gpu":
        from fastapi import FastAPI, HTTPException

        from app.routing import LOCAL_ROUTERS, Task, replay_choice
        from app.routing_profile import replay_profile

        app = FastAPI()
        counter = {"calls": 0}

        @app.get("/health")
        def health():
            return {"status": "ok", "models": {"h2o": True, "liquid": False}}

        @app.get("/stats")
        def stats():
            return counter

        @app.post("/v1/systemone/h2o")
        async def infer(payload: dict):
            counter["calls"] += 1
            await asyncio.sleep(0.65)
            text = json.loads(payload["state"])["request"]
            if "GPU_FAIL" in text:
                raise HTTPException(503, "synthetic fixture failure")
            profile = replay_profile(Task(text=text), replay_choice(Task(text=text)))
            return {
                "model": LOCAL_ROUTERS["h2o"],
                "answers": {k: {"choice": v} for k, v in profile.model_dump().items()},
                "usage": {"input_tokens": 128, "output_tokens": 7},
                "latency_ms": 15,
                "deployment": "SYNTHETIC BROWSER TEST; NOT A GPU BENCHMARK",
            }
    else:
        from app import gateway_main as main
        from app.auth import SQLiteStore, save_user

        class FixtureBedrock:
            def __init__(self, *, read_timeout=40):
                self.read_timeout = read_timeout

            def converse(self, **kwargs):
                time.sleep(0.3)
                task = kwargs["messages"][-1]["content"][0]["text"]
                return {
                    "output": {
                        "message": {
                            "content": [
                                {
                                    "text": "[本地联调测试桩 · 非真实模型输出]\n\n"
                                    "已完成请求：按 event_id 去重，使用 Decimal 处理金额，并在事务内写入账本。\n"
                                    "这是用于验证浏览器、独立 API 和决策服务连接的合成响应。"
                                }
                            ]
                        }
                    },
                    "stopReason": "max_tokens" if "INCOMPLETE" in task else "end_turn",
                    "usage": {"inputTokens": 128, "outputTokens": 64},
                }

            def converse_stream(self, **kwargs):
                response = self.converse(**kwargs)

                def stream():
                    text = response["output"]["message"]["content"][0]["text"]
                    for start in range(0, len(text), 24):
                        time.sleep(0.03)
                        yield {"contentBlockDelta": {"delta": {"text": text[start:start + 24]}}}
                    yield {"messageStop": {"stopReason": response["stopReason"]}}
                    yield {"metadata": {"usage": response["usage"]}}

                return {"stream": stream()}

            def close(self):
                pass

        store = SQLiteStore(os.environ["AUTH_DB_PATH"])
        save_user(
            store,
            "tester",
            "gateway-browser-fixture-only",
        )
        user = store.get("USER#tester")
        user["role"] = "admin"
        store.put(user)
        main.create_client = FixtureBedrock
        app = main.app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def smoke(output):
    from playwright.sync_api import expect, sync_playwright

    output.mkdir(parents=True, exist_ok=False)
    ports = []
    while len(ports) < 3:
        port = free_port()
        if port not in ports:
            ports.append(port)
    gpu_port, api_port, frontend_port = ports
    processes = []
    with tempfile.TemporaryDirectory(prefix="gateway-smoke-") as scratch, ExitStack() as stack:
        env = dict(os.environ)
        for name in (
            "AUTH_TABLE",
            "AUTH_ORIGIN",
            "GPU_STATE_TABLE",
            "GPU_INSTANCE_ID",
            "GPU_PROVIDER",
            "JEV_API_KEY",
            "JEV_API_KEY_FILE",
            "JEV_FUNCTION_NAME",
        ):
            env.pop(name, None)
        env.update(
            {
                "AUTH_DB_PATH": str(Path(scratch) / "auth.sqlite3"),
                "GATEWAY_DB_PATH": str(Path(scratch) / "gateway.sqlite3"),
                "AUTH_COOKIE_SECURE": "false",
                "SERVE_FRONTEND": "false",
                "BEDROCK_ENABLED": "false",
                "OPEN_MODELS_URL": f"http://127.0.0.1:{gpu_port}",
                "GATEWAY_DECISION_PROVIDER": "h2o",
                "AWS_EC2_METADATA_DISABLED": "true",
                "PYTHONPATH": str(ROOT),
            }
        )
        commands = [
            [
                sys.executable,
                "-m",
                "scripts.check_gateway_browser",
                "--serve",
                "gpu",
                "--port",
                str(gpu_port),
            ],
            [
                sys.executable,
                "-m",
                "scripts.check_gateway_browser",
                "--serve",
                "api",
                "--port",
                str(api_port),
            ],
            [
                sys.executable,
                "-m",
                "scripts.serve_gateway",
                "--port",
                str(frontend_port),
                "--api",
                f"http://127.0.0.1:{api_port}",
            ],
        ]
        try:
            for index, command in enumerate(commands):
                log = stack.enter_context((output / f"process-{index}.log").open("w"))
                processes.append(
                    subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=log)
                )
            base = f"http://127.0.0.1:{frontend_port}"
            with httpx.Client(trust_env=False) as client:
                for _ in range(100):
                    try:
                        if client.get(base + "/api/live").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Smoke test services did not start")
            errors, requests = [], []
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("request", lambda request: requests.append(request.url))
                page.goto(base)
                page.locator('input[name="username"]').fill("tester")
                page.locator('input[name="password"]').fill("gateway-browser-fixture-only")
                page.locator("#login-submit").click()
                expect(page.locator("#app")).to_be_visible()
                expect(page.locator("#health")).to_have_class("status ready")
                expect(page.locator("#max-output")).to_have_value("4096")
                expect(page.locator("#task")).to_have_value("")
                page.locator("#max-cost").fill("0.05")
                page.locator('[data-example="support"]').click()
                expect(page.locator("#max-output")).to_have_value("4096")
                expect(page.locator("#expected-output")).to_have_value("512")
                page.locator('[data-example="code"]').click()
                expect(page.locator("#max-output")).to_have_value("16384")
                expect(page.locator("#expected-output")).to_have_value("8192")
                expect(page.locator("#max-cost")).to_have_value("0.05")
                page.locator("#max-cost").fill("")
                page.locator(".advanced summary").click()
                page.locator("#benchmark-release").select_option("2025-04-02")
                page.locator(".advanced summary").click()
                page.locator("#run").click()
                # This must be visible while the independent GPU request is still running.
                expect(page.locator(".stage.started")).to_be_visible()
                expect(page.locator("#run")).to_be_enabled(timeout=20000)
                expect(page.locator("#run-badge")).to_have_text("已完成")
                expect(page.locator(".profile-chips span")).to_have_count(7)
                expect(page.locator(".output")).to_contain_text("本地联调测试桩")
                expect(page.locator("#stages .completed")).to_have_count(4)
                expect(page.locator("#conversation-state")).to_contain_text("带入 0 轮历史")
                page.evaluate("scrollTo(0, 0)")
                page.screenshot(path=str(output / "desktop.png"), full_page=True)
                page.locator('[data-tab="candidates"]').click()
                expect(page.locator(".candidate-table .selected")).to_have_count(1)
                page.evaluate("scrollTo(0, 0)")
                page.screenshot(path=str(output / "candidates.png"), full_page=True)
                page.locator('[data-tab="timing"]').click()
                expect(page.locator("#timing")).to_contain_text("GPU 服务自报耗时：15.0 ms")
                page.evaluate("scrollTo(0, 0)")
                page.screenshot(path=str(output / "timings.png"), full_page=True)
                with page.expect_download() as download:
                    page.locator("#export").click()
                record = json.loads(Path(download.value.path()).read_text())
                assert "route_token" not in json.dumps(record)
                assert record["events"][-1]["data"]["status"] == "executed"
                assert any(e["type"] == "delta" for e in record["events"])
                chosen = next(
                    e["data"] for e in record["events"]
                    if e.get("stage") == "selection" and e.get("status") == "completed"
                )
                assert chosen["selection"]["benchmark_group"]["release"] == "2025-04-02"
                (output / "synthetic-run.json").write_text(
                    json.dumps(record, ensure_ascii=False, indent=2)
                )
                page.locator(".advanced summary").click()
                page.locator("#min-success").fill("0.99")
                page.locator("#run").click()
                expect(page.locator("#run")).to_be_enabled(timeout=20000)
                expect(page.locator("#run-badge")).to_have_text("无符合条件模型")
                page.locator("#min-success").fill("")
                page.locator("#task").fill("GPU_FAIL")
                page.locator("#route-only").click()
                expect(page.locator("#run")).to_be_enabled(timeout=20000)
                expect(page.locator("#request-error")).to_contain_text("GPU 决策服务调用失败")
                page.locator("#task").fill("编写 Python INCOMPLETE")
                page.locator("#run").click()
                expect(page.locator("#run")).to_be_enabled(timeout=20000)
                expect(page.locator("#run-badge")).to_have_text("输出未完成")
                expect(page.locator("#conversation-state")).to_contain_text("带入 1 轮历史")
                page.locator("#mode").select_option("replay")
                page.locator("#run").click()
                expect(page.locator("#run")).to_be_enabled(timeout=20000)
                expect(page.locator("#run-badge")).to_contain_text("回放")
                page.locator('[data-page="performance"]').click()
                expect(page.locator("#session-metrics .metric-value").first).to_have_text("4")
                expect(page.locator("#session-metrics")).to_contain_text("1 次回放不计入")
                page.evaluate("scrollTo(0, 0)")
                page.screenshot(path=str(output / "performance.png"), full_page=True)
                page.reload()
                expect(page.locator("#app")).to_be_visible()
                expect(page.locator("#session-metrics .metric-value").first).to_have_text("4")
                expect(page.locator("#history tbody tr")).to_have_count(5)
                page.locator(f'[data-run="{record["events"][0]["request_id"]}"]').click()
                expect(page.locator("#history-detail")).to_be_visible()
                expect(page.locator("#history-detail-content")).to_contain_text("本地联调测试桩")
                page.locator("#review-passed").select_option("false")
                page.locator("#review-rubric").fill("合成响应只验证链路，不能作为业务通过")
                page.locator("#review-form button").click()
                expect(page.locator("#notification")).to_have_text("人工验收已保存")
                page.screenshot(path=str(output / "saved-run.png"), full_page=True)
                page.locator("#close-history-detail").click()
                page.locator('[data-page="workspace"]').click()
                page.locator("#new-session").click()
                page.locator("#session-title").fill("独立验收会话")
                page.locator("#session-form button.primary").click()
                expect(page.locator("#session-select option:checked")).to_have_text("独立验收会话")
                page.locator("#rename-session").click()
                page.locator("#session-title").fill("重命名后的会话")
                page.locator("#session-form button.primary").click()
                expect(page.locator("#session-select option:checked")).to_have_text("重命名后的会话")
                page.locator("#template-category").select_option("代码")
                page.locator("#template-search").fill("HMAC")
                expect(page.locator("#examples button")).to_have_count(1)
                page.locator("#template-search").fill("")
                page.locator("#template-category").select_option("all")
                expect(page.locator("#examples button")).to_have_count(
                    len(json.loads((ROOT / "app/gateway_templates.json").read_text()))
                )
                page.locator('[data-page="evaluations"]').click()
                for checkbox in page.locator('input[name="eval-case"]').all():
                    if checkbox.is_checked():
                        checkbox.uncheck()
                for case in ("summarize", "sql-report"):
                    page.locator(f'input[name="eval-case"][value="{case}"]').check()
                expect(page.locator("#evaluation-budget")).to_contain_text("2 次 GPU 调用尝试")
                page.locator("#evaluation-start").click()
                expect(page.locator("#evaluation-result h2")).to_have_text("评测结果 · 已完成", timeout=20000)
                expect(page.locator("#evaluation-result")).to_contain_text("2 / 2")
                page.screenshot(path=str(output / "evaluation.png"), full_page=True)
                from app.gateway_effect_cases import suite

                expect(page.locator('input[name="effect-case"]')).to_have_count(len(suite()["items"]))
                expect(page.locator("#effect-token-cap")).to_have_value("16384")
                page.locator("#effect-clear").click()
                for checkbox in page.locator('input[name="effect-provider"]').all():
                    if checkbox.input_value() != "h2o":
                        checkbox.uncheck()
                for case in ("greeting", "web-search"):
                    page.locator(f'input[name="effect-case"][value="{case}"]').check()
                page.locator("#effect-start").click()
                expect(page.locator("#effect-result h2")).to_have_text("效果结果 · 已完成", timeout=20000)
                expect(page.locator("#effect-result")).to_contain_text("目标调用尝试 1")
                expect(page.locator("#effect-result")).to_contain_text("能力/评测缺口")
                expect(page.locator("#effect-result")).to_contain_text("任务类型判断正确/已检查")
                expect(page.locator("#effect-overall")).to_contain_text("整体验收通过/全部场景")
                expect(page.locator("#effect-overall")).to_contain_text("0/2 (0.0%)")
                effect_row = page.locator("#effect-result .evaluation-row").first
                effect_row.locator("summary").first.click()
                review = effect_row.locator("[data-effect-review]")
                review.locator('[name="status"]').select_option("fail")
                review.locator('[name="reason"]').fill("合成响应谈论幂等而不是问候，按任务要求不通过。")
                review.locator("button").click()
                expect(page.locator("#effect-result")).to_contain_text("人工评审：tester")
                with page.expect_download() as downloaded:
                    page.locator("#export-effect").click()
                effect = json.loads(Path(downloaded.value.path()).read_text())
                assert effect["summary"][0]["quality_pass_rate"] == 0
                assert effect["summary"][0]["gaps"] == 1
                assert effect["max_output_tokens"] == 16384
                assert effect["summary"][0]["answer_failed"] == 1
                assert effect["summary"][0]["output_incomplete"] == 0
                assert effect["overall_metric_version"] == "end-to-end-v1"
                assert effect["summary"][0]["overall_fail"] == 1
                assert effect["summary"][0]["overall_unmeasured"] == 1
                assert effect["summary"][0]["overall_total"] == 2
                expect(effect_row.locator("summary").first).to_contain_text("整体未通过")
                assert "route_token" not in json.dumps(effect)
                page.screenshot(path=str(output / "effect-evaluation.png"), full_page=True)
                page.locator('[data-page="account"]').click()
                expect(page.locator("#admin-panel")).to_be_visible()
                page.locator("#new-user-name").fill("reviewer")
                page.locator("#new-user-password").fill("reviewer-browser-fixture-only")
                page.locator("#user-form button").click()
                expect(page.locator("#users-table")).to_contain_text("reviewer")
                page.locator('[data-user="reviewer"]').click()
                expect(page.locator('[data-user="reviewer"]')).to_have_text("启用")
                page.locator('[data-user="reviewer"]').click()
                expect(page.locator('[data-user="reviewer"]')).to_have_text("停用并撤销登录")
                page.screenshot(path=str(output / "accounts.png"), full_page=True)
                page.locator("#logout").click()
                page.locator('input[name="username"]').fill("reviewer")
                page.locator('input[name="password"]').fill("reviewer-browser-fixture-only")
                page.locator("#login-submit").click()
                expect(page.locator("#app")).to_be_visible()
                expect(page.locator("#admin-panel")).to_be_hidden()
                page.locator('[data-page="performance"]').click()
                expect(page.locator("#session-metrics .metric-value").first).to_have_text("0")
                expect(page.locator("#history")).to_contain_text("没有保存的请求")
                page.locator("#logout").click()
                page.locator('input[name="username"]').fill("tester")
                page.locator('input[name="password"]').fill("gateway-browser-fixture-only")
                page.locator("#login-submit").click()
                expect(page.locator("#app")).to_be_visible()
                expect(page.locator("#session-metrics .metric-value").first).to_have_text("4")
                page.locator('[data-page="models"]').click()
                page.locator("#category").select_option("customer_support")
                page.locator("#model-search").fill("nova micro")
                expect(page.locator("#model-table")).to_contain_text("已映射评测")
                page.locator(".model-evidence summary").first.click()
                expect(page.locator("#model-table")).to_contain_text("2025-04-02")
                page.screenshot(path=str(output / "mapping-nova.png"), full_page=True)
                page.locator("#model-search").fill("qwen3-coder-next")
                expect(page.locator("#model-table")).to_contain_text("型号资料已核验")
                page.locator("#model-search").fill("fable")
                page.locator("#evidence-filter").select_option("configuration_pending")
                expect(page.locator("#model-table")).to_contain_text("配置待核验")
                page.locator("#evidence-filter").select_option("all")
                page.locator("#model-search").fill("")
                page.locator("#category").select_option("embedding")
                expect(page.locator("#model-table")).to_contain_text("个配置通过基础筛选")
                cohere = page.locator('[data-model-id="cohere.embed-english-v3"]')
                cohere.locator(".model-evidence summary").click()
                expect(cohere).to_contain_text("en 57.12")
                expect(cohere).to_contain_text("nDCG@10 × 100")
                page.set_viewport_size({"width": 390, "height": 844})
                for section in ("models", "performance", "evaluations", "account", "workspace"):
                    page.locator(f'[data-page="{section}"]').click()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                        section
                    )
                page.locator(".advanced summary").click()
                page.locator('[data-tab="response"]').click()
                page.evaluate("scrollTo(0, 0)")
                page.screenshot(path=str(output / "mobile.png"), full_page=True)
                assert not errors, errors
                assert all(url.startswith(base) for url in requests), requests
                browser.close()
            stats = httpx.get(f"http://127.0.0.1:{gpu_port}/stats").json()
            assert stats["calls"] == 7, stats
            verification = {
                "fixture": "synthetic; not a GPU performance benchmark",
                "processes": [
                    "independent static frontend/proxy",
                    "API with Bedrock fixture",
                    "GPU HTTP fixture",
                ],
                "gpu_fixture_calls": stats["calls"],
                "real_model_calls": 0,
                "browser_errors": errors,
                "checks": [
                    "explicit benchmark release preserved in routing",
                    "Nova benchmark sources and category scores",
                    "reference-only and pending-configuration filters",
                    "authentication",
                    "database persistence after reload and relogin",
                    "user history isolation and admin-only user management",
                    "session creation and rename",
                    "searchable general-purpose and multimodal task templates",
                    "persisted classifier evaluation with two synthetic calls",
                    "independent human business review",
                    "stream before inference completes",
                    "one GPU call per run",
                    "selection evidence",
                    "execution and timing",
                    "no route",
                    "GPU failure",
                    "partial output",
                    "replay excluded from metrics",
                    "no direct browser GPU access",
                    "export without receipts",
                    "mobile overflow",
                ],
            }
            (output / "verification.json").write_text(
                json.dumps(verification, ensure_ascii=False, indent=2)
            )
            print(json.dumps(verification, ensure_ascii=False))
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", choices=("gpu", "api"))
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.port)
    elif args.output:
        smoke(args.output)
    else:
        parser.error("--output is required")


if __name__ == "__main__":
    main()
