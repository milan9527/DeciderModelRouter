"""Check consistent payment timings across rounds, the table, rejudgments and exports."""

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

PROVIDERS = ("decider", "jev", "bedrock")
STAGES = ("intake", "verify", "approve", "execute", "close")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frontend", type=Path)
    parser.add_argument("--chromium", type=Path)
    parser.add_argument("--live", action="store_true", help="One real fixed payment comparison")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new output directory to preserve earlier evidence.")
    args.output.mkdir(parents=True)
    credentials = json.loads(args.credentials.read_text())
    report = {"mode": "live" if args.live else "synthetic_ui_only", "runs": []}
    errors, forbidden = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            **({"executable_path": str(args.chromium)} if args.chromium else {}),
        )
        page = browser.new_page(viewport={"width": 1600, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        if args.frontend:
            def frontend(route):
                path = urlparse(route.request.url).path
                if path == "/" or path.startswith("/static/"):
                    asset = args.frontend / ("index.html" if path == "/" else path.lstrip("/"))
                    route.fulfill(path=asset)
                else:
                    route.continue_()
            page.route(args.url.rstrip("/") + "/**", frontend)

        def prohibit(route):
            forbidden.append(route.request.url)
            route.fulfill(status=409, json={"detail": "Timing test never changes GPU lifecycle"})

        page.route("**/api/gpu/start", prohibit)
        page.route("**/api/gpu/stop", prohibit)
        if not args.live:
            def synthetic(route):
                body = route.request.post_data_json
                loop = route.request.url.endswith("/tick")
                # Delayed replay responses are explicit UI fixtures, never benchmark results.
                time.sleep(0.12)
                if (
                    not loop and body.get("input_profile") == "optimized"
                    and body["provider"] == "jev" and body["stage"] == "approve"
                ):
                    route.fulfill(status=503, json={"detail": "Synthetic timing test failure"})
                    return
                reply = route.fetch(post_data=json.dumps({**body, "mode": "replay"}))
                assert reply.ok, reply.status
                data = reply.json()
                decision = data.get("decision") if loop else data
                if decision:
                    ms = (
                        {"decider": 50, "jev": 80, "bedrock": 110}[body["provider"]]
                        + STAGES.index(decision["stage"]) * 3
                        + body.get("state", {}).get("decisions", 0) * 7
                        + (20 if body.get("input_profile") == "optimized" else 0)
                    )
                    decision.update(
                        source="live", model="synthetic-timing-fixture-not-a-model",
                        timings={"backend_total_ms": ms},
                    )
                route.fulfill(json=data)

            page.route("**/api/crypto/decision", synthetic)
            page.route("**/api/crypto/loop/tick", synthetic)

        page.goto(args.url + "/#crypto", wait_until="domcontentloaded")
        page.locator("#login-username").fill(credentials["username"])
        page.locator("#login-password").fill(credentials["password"])
        page.locator("#login-submit").click()
        expect(page.locator(".cx-case")).to_have_count(4)
        page.locator('[data-cx-mode="live"]').click()

        def export():
            with page.expect_download() as download:
                page.locator("[data-cx-export]").click()
            return json.loads(Path(download.value.path()).read_text())

        def verify(result, loop):
            latest = [row for row in result["dialogues"] if row["run"] == result["dialogueRun"]]
            for row in latest:
                for provider, side in row["sides"].items():
                    assert not side["pending"] and side["elapsedMs"] >= 0
                    output = page.locator(
                        f'[data-cx-dialogue-turn="{row["number"]}"] '
                        f'[data-cx-dialogue-output="{provider}"]'
                    )
                    request_label = "整轮请求（含执行）" if loop else "整次请求（含网络）"
                    expect(output.locator(".cx-request-time")).to_contain_text(request_label)
                    decision = side.get("result")
                    if decision:
                        assert decision["source"] == "live"
                        assert decision["client_ms"] == side["elapsedMs"]
                        assert decision["dialogue_round"] == row["number"]
                        expect(output.locator("[data-cx-decision-time]")).to_contain_text("决策耗时")
                        if loop:
                            assert side["response"]["decision"]["client_ms"] == side["elapsedMs"]
                            assert side["executions"][-1]["tick_elapsed_ms"] == side["elapsedMs"]
                    else:
                        assert not side.get("error") if args.live else True
                        expect(output.locator("[data-cx-decision-time]")).not_to_contain_text(
                            "决策耗时"
                        )
            for provider in PROVIDERS:
                for stage, result_row in result["results"][provider].items():
                    cell = page.locator(f'[data-cx-compare="{provider}-{stage}"]')
                    if result_row.get("source") != "live":
                        expect(cell.locator(".cx-table-request")).to_have_count(0)
                        continue
                    row = next(r for r in latest if r["number"] == result_row["dialogue_round"])
                    side = row["sides"][provider]
                    assert side["result"] == result_row
                    output = page.locator(
                        f'[data-cx-dialogue-turn="{row["number"]}"] '
                        f'[data-cx-dialogue-output="{provider}"]'
                    )
                    expect(cell.locator("[data-cx-decision-time]")).to_have_text(
                        output.locator("[data-cx-decision-time]").inner_text()
                    )
                    expect(cell.locator(".cx-table-request")).to_have_text(
                        output.locator(".cx-request-time").inner_text()
                    )
                    if loop:
                        expect(cell.locator(".cx-table-round")).to_have_text(
                            f'第 {row["number"]} 轮 · 最近一次判断'
                        )
            expect(page.locator(".cx-compare-table caption")).to_contain_text(
                f'第 {result["dialogueRun"]} 次对比'
            )

        def run(kind, case="pending"):
            page.locator(f'[data-cx-case="{case}"]').click()
            page.locator("[data-cx-test-kind]").select_option(kind)
            page.locator("[data-cx-start]").click()
            expect(page.locator("[data-cx-start]")).to_be_enabled(timeout=240000)
            result = export()
            report["runs"].append(result)
            (args.output / "results.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            verify(result, kind == "loop")
            return result

        run("workflow")
        page.locator(".cx-dialogue-feed").evaluate("(el) => el.scrollTop = 0")
        page.screenshot(path=str(args.output / "fixed-desktop.png"), full_page=True)
        if not args.live:
            current = run("optimized")
            assert current["results"]["jev"]["approve"]["error"] == "Synthetic timing test failure"
            page.locator("[data-cx-dialogue-run]").select_option("1")
            expect(page.locator("[data-cx-history-notice]")).to_contain_text(
                f'下方流程表为第 {current["dialogueRun"]} 次对比'
            )
            expect(page.locator(".cx-compare-table caption")).to_contain_text(
                f'第 {current["dialogueRun"]} 次对比'
            )
            page.locator("[data-cx-dialogue-run]").select_option(str(current["dialogueRun"]))
            expect(page.locator("[data-cx-history-notice]")).to_have_count(0)
            loop = run("loop", "refund")
            for provider in PROVIDERS:
                rows = [r["sides"][provider] for r in loop["dialogues"] if provider in r["sides"]]
                decisions = [r["result"] for r in rows if r.get("result")]
                assert len(decisions) > 5
                assert any(r.get("response", {}).get("event") for r in rows)
                assert loop["executions"][provider]["loopState"]["status"] == "complete"
                assert all(
                    r["decision_ms"] is not None
                    for r in loop["executions"][provider]["timeline"]
                    if r["kind"] == "decision"
                )
            page.screenshot(path=str(args.output / "loop-desktop.png"), full_page=True)
        for width in (390, 320):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), width
            page.screenshot(path=str(args.output / f"mobile-{width}.png"), full_page=True)
        assert not errors, errors
        assert not forbidden, forbidden
        report["verification"] = {
            "passed": True, "browser_errors": errors, "gpu_changes": forbidden,
            "round_table_export_agree": True,
            "loop_history_failure_and_events": not args.live,
        }
        (args.output / "verification.json").write_text(json.dumps(
            {"mode": report["mode"], **report["verification"]}, ensure_ascii=False, indent=2,
        ))
        browser.close()
    print(json.dumps({"passed": True, "mode": report["mode"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
