"""Verify workflow comparison UI; live mode retains every actual run and failure."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from app.comparison_workflows import CASES, Workflow


def fixture(body):
    case = CASES[body["case_id"]]
    task = body["task"]
    record = {"id": "browser-workflow-fixture", "version": "original-contract-v1", "status": "done",
              "case_id": case["id"], "group": case["group"], "title": case["title"], "goal": case["goal"],
              "task": task, "variant": body["variant"], "initial": case["initial"], "stages": case["steps"],
              "total_ms": 5000, "outcomes": {}}
    for provider in ("decider", "liquid", "h2o", "jev", "gpt"):
        w = Workflow(case, task)
        outcome = {"status": "done", "steps": [], "decision_ms": 0, "execution_ms": 0,
                   "matched": 0, "scored_questions": 0, "total_ms": 1000, "final": None}
        for stage in case["steps"]:
            step = {**stage, "status": "ok", "timings": {"processing_ms": 50}, "decision_ms": 50, "execution_ms": 10}
            if stage["kind"] == "execution":
                step["request"] = {"task": task}
                w.target_result = {"text": "合成 UI 测试结果", "complete": True}
                step["execution"] = {"label": "目标任务已返回", **w.target_result}
                step["timings"] = {}
            else:
                payload, expected = w.prepare(stage["id"])
                if body["variant"] == "reversed":
                    for q in payload["questions"].values():
                        q["criteria"] = dict(reversed(list(q["criteria"].items())))
                if case["group"] == "routing":
                    choices = {"action": "customer_support" if stage["id"] == "workflow" else w.pool["candidates"][0]["candidate_id"]}
                else:
                    choices = expected or {k: next(iter(q["criteria"])) for k, q in payload["questions"].items()}
                step.update(request=payload, choices=choices, expected=expected, question_count=len(choices),
                            matched=sum(v == expected[k] for k, v in choices.items()) if expected else None,
                            output={"model": "browser-fixture", "answers": {k: {"choice": v} for k, v in choices.items()}})
                step["execution"] = w.apply(stage["id"], choices)
                outcome["decision_ms"] += 50
                if step["matched"] is not None:
                    outcome["matched"] += step["matched"]
                    outcome["scored_questions"] += step["question_count"]
            step["label"] = step["execution"]["label"]
            outcome["steps"].append(step)
        outcome["final"] = w.finish()
        outcome["executed_steps"] = len(outcome["steps"])
        if case["group"] == "routing":
            outcome["target_request"] = w.target_request()
        if provider == "h2o":
            outcome["status"] = "error"
            outcome["final"]["complete"] = False
            outcome["steps"][-1] = {**case["steps"][-1], "status": "error", "timings": {}, "error": "合成测试：模型不可用"}
            outcome.pop("target_request", None)
        record["outcomes"][provider] = outcome
    return record


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--credentials", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--live", action="store_true")
    p.add_argument("--all-cases", action="store_true")
    p.add_argument("--original-cases", action="store_true")
    p.add_argument("--case")
    p.add_argument("--chromium")
    a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=True)
    credentials = json.loads(a.credentials.read_text())
    errors, results, submissions, target_calls = [], {}, [], []
    current_fixture = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"], **({"executable_path": a.chromium} if a.chromium else {}))
        page = browser.new_page(viewport={"width": 1760, "height": 1250})
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("request", lambda req: target_calls.append(req.post_data_json)
                if req.url.endswith("/api/routing/execute") else None)

        def mock(route):
            nonlocal current_fixture
            if route.request.method == "POST":
                body = route.request.post_data_json
                submissions.append(body)
                current_fixture = fixture(body)
                return route.fulfill(status=202, json={**current_fixture, "status": "running"})
            route.fulfill(json=current_fixture)

        def capture(reply):
            if "/api/comparison/runs/" in reply.url and reply.status == 200:
                body = reply.json()
                (a.output / "latest.json").write_text(json.dumps(body, ensure_ascii=False, indent=2))
                if body["status"] == "done":
                    results[body["id"]] = body
                    (a.output / "results.json").write_text(json.dumps({"live": a.live, "page_errors": errors, "results": list(results.values())}, ensure_ascii=False, indent=2))

        if not a.live:
            page.route("**/api/comparison/runs**", mock)
            page.route("**/api/routing/execute", lambda route: route.fulfill(
                json={"text": "合成目标执行结果", "complete": True, "backend_total_ms": 12}))
        page.on("response", capture)
        page.goto(a.url.rstrip("/") + "/#comparison", wait_until="domcontentloaded")
        page.locator("#login-username").fill(credentials["username"])
        page.locator("#login-password").fill(credentials["password"])
        page.locator("#login-submit").click()
        expect(page.locator("[data-c-run]")).to_be_visible(timeout=30000)
        selected = list(CASES) if a.all_cases else ["payment-pending", "routing-support", "device-relative", "temperature-presets"]
        if a.original_cases:
            selected = [c["id"] for c in CASES.values() if c.get("original")]
        if a.case:
            selected = [a.case]
        for case_id in selected:
            case = CASES[case_id]
            tab = "device_control" if case["group"] == "temperature_multiselect" else case["group"]
            page.locator(f'[data-c-group="{tab}"]').click()
            page.locator("[data-c-case]").select_option(case_id)
            expect(page.locator("[data-c-step]")).to_have_count(len(case["steps"]))
            expect(page.locator(".mc-model")).to_have_count(5)
            if case.get("original"):
                expect(page.locator("[data-c-task]")).to_have_attribute("readonly", "")
                expect(page.locator("[data-c-order]")).to_be_disabled()
            page.locator("[data-c-run]").click()
            expect(page.locator("[data-c-run]")).to_be_enabled(timeout=700000 if a.live else 15000)
            expect(page.locator(".mc-summary")).to_contain_text("5/5 模型执行结束")
            if case["group"] == "routing":
                assert not target_calls
                if not a.live:
                    page.locator('[data-c-target="decider"]').click()
                    expect(page.locator("[data-c-run]")).to_be_enabled()
                    expect(page.locator(".mc-target").first).to_contain_text("目标执行完成")
                    assert len(target_calls) == 1 and target_calls[0]["strategy"] == "original"
                    target_calls.clear()
            page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
            page.screenshot(path=str(a.output / f"{case_id}-results.png"), full_page=True)
            for index, stage in enumerate(case["steps"]):
                page.locator(f'[data-c-step="{index}"]').click()
                expect(page.locator(".mc-step-card")).to_have_count(5)
                if index == 0 or stage["id"] in {"execute", "model", "temperature", "selection"}:
                    page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
                    page.screenshot(path=str(a.output / f'{case_id}-{stage["id"]}.png'), full_page=True)
            print(json.dumps({"case": case_id, "summary": page.locator(".mc-summary").inner_text()}, ensure_ascii=False), flush=True)
        if not a.live:
            page.locator("[data-c-task]").fill("只选择24度。<script>throw Error('unsafe')</script>")
            expect(page.locator(".mc-final")).to_have_count(0)
            page.locator("[data-c-order]").select_option("reversed")
            page.locator("[data-c-run]").click()
            expect(page.locator("[data-c-run]")).to_be_enabled(timeout=15000)
            expect(page.locator(".mc-goal").first).to_contain_text("自定义任务")
            assert submissions[-1]["variant"] == "reversed"
            with page.expect_download() as download:
                page.locator("[data-c-export]").click()
            assert download.value.suggested_filename.endswith(".json")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(a.output / "mobile.png"), full_page=True)
        assert not errors, errors
        if a.live:
            assert not target_calls, "Routing comparisons must not execute targets automatically"
            assert len(results) == len(selected)
            source = json.loads(Path("tests/fixtures/original_comparison_inputs.json").read_text())
            checked = 0
            for run in results.values():
                for outcome in run["outcomes"].values():
                    workflow = None
                    for step in outcome["steps"]:
                        if run["group"] not in {"payment", "routing"} or not step.get("request"):
                            continue
                        candidates = [r for r in source["cases"] if r["case_id"] == run["case_id"] and r["stage"] == step["id"] and r.get("workflow") == (workflow if step["id"] == "model" else None)]
                        assert len(candidates) == 1
                        assert json.dumps(step["request"], ensure_ascii=False) == json.dumps(candidates[0]["model_input"], ensure_ascii=False)
                        assert step["original_input_hash"] == candidates[0]["input_hash"]
                        checked += 1
                        if step["id"] == "workflow" and step.get("choices"):
                            workflow = step["choices"]["action"]
            print(json.dumps({"original_input_checks": checked}), flush=True)
            for run in results.values():
                for outcome in run["outcomes"].values():
                    assert outcome["decision_ms"] == sum(s.get("decision_ms", 0) for s in outcome["steps"])
                    assert outcome["final"] is not None
        (a.output / "results.json").write_text(json.dumps({"live": a.live, "page_errors": errors, "results": list(results.values()), "synthetic_submissions": len(submissions)}, ensure_ascii=False, indent=2))
        browser.close()
    print(json.dumps({"live": a.live, "runs": len(results), "page_errors": len(errors)}))


if __name__ == "__main__":
    main()
