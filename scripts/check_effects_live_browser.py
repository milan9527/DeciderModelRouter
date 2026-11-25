"""Read-only production evaluation UI verification (login and downloads; no model calls)."""

import argparse
import hashlib
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from app.gateway_effect_cases import suite


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--credentials", type=Path, required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    base = args.url.rstrip("/")
    credentials = json.loads(args.credentials.read_text())
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        response = page.goto(base + "/")
        assert response.status == 200 and page.url == base + "/"
        page.locator('input[name="username"]').fill(credentials["username"])
        page.locator('input[name="password"]').fill(credentials["password"])
        page.locator("#login-submit").click()
        expect(page.locator("#app")).to_be_visible()
        page.locator('[data-page="evaluations"]').click()
        expect(page.locator('input[name="effect-case"]')).to_have_count(len(suite()["items"]))
        expect(page.locator("#effect-token-cap")).to_have_value("16384")
        page.locator("#effect-family").select_option("音频与语音")
        assert page.locator("[data-effect-family]:visible").count() == 5
        page.locator("#effect-family").select_option("")
        page.locator(f'[data-effect="{args.job_id}"]').click()
        expect(page.locator("#effect-result h2")).to_have_text("效果结果 · 已完成", timeout=30000)
        expect(page.locator("#effect-result")).to_contain_text("任务类型判断正确/已检查")
        expect(page.locator("#effect-result")).to_contain_text("完整答案不合格/输出未完成")
        page.locator("#effect-result").scroll_into_view_if_needed()
        page.screenshot(path=str(args.output / "effects-desktop.png"), full_page=True)
        with page.expect_download() as download:
            page.locator("#export-effect").click()
        job = json.loads(Path(download.value.path()).read_text())
        assert job["id"] == args.job_id and len(job["dataset_version"]) == 16
        assert "route_token" not in json.dumps(job)
        expect(page.locator("#effect-result")).to_contain_text(f"本批输出上限 {job['max_output_tokens']} tokens")
        incomplete = next((r for r in job["results"] if r["arm"] == "routed" and r["status"] == "incomplete"), None)
        if incomplete:
            summary = page.locator(f'[data-effect-row="{incomplete["id"]}"] > summary')
            expect(summary).to_contain_text("输出未完成（含截断）")
            expect(summary).not_to_contain_text("失败")
        page.reload()
        expect(page.locator("#app")).to_be_visible()
        page.locator(f'[data-effect="{args.job_id}"]').click()
        expect(page.locator("#effect-result h2")).to_have_text("效果结果 · 已完成")
        first = page.locator("#effect-result .evaluation-row").first
        first.locator("summary").first.click()
        expect(first).to_contain_text("助手辅助评审")
        first.scroll_into_view_if_needed()
        page.screenshot(path=str(args.output / "effects-evidence.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(args.output / "effects-mobile.png"), full_page=True)
        assets = {}
        for name in ("index.html", "gateway.js", "gateway.css", "workspace.js", "mark.svg"):
            fetched = page.request.get(base + "/gateway/" + name)
            assert fetched.status == 200
            local = Path("frontend/gateway", name).read_bytes()
            digest = hashlib.sha256(fetched.body()).hexdigest()
            assert digest == hashlib.sha256(local).hexdigest(), name
            assets[name] = digest
        assert not errors, errors
        report = {
            "url": base + "/", "root_status": 200, "redirect": False,
            "job_id": job["id"], "status": job["status"],
            "scenario_count": len(suite()["items"]), "case_count": len(job["cases"]),
            "dataset_version": job["dataset_version"], "new_model_calls": 0,
            "current_suite_version": suite()["version"],
            "reload_persisted": True, "export_without_receipts": True,
            "review_methods_visible": True, "mobile_overflow": False,
            "new_evaluation_default_output_cap": 16384,
            "historical_output_cap": job["max_output_tokens"],
            "incomplete_distinct_from_bad_answer": True,
            "browser_errors": errors, "deployed_asset_sha256": assets,
        }
        (args.output / "verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        browser.close()
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
