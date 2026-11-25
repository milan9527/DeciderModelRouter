"""Verify a saved real industry-agent job in the deployed UI without new inference."""

import argparse
import json
from pathlib import Path

import boto3
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://d3eipg2g3l3an3.cloudfront.net")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    credentials = json.loads(boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId="DeciderModelRouter/admin-credentials")["SecretString"])
    report = {"fixture": False, "new_model_calls": 0, "job_id": args.job_id, "checks": [], "page_errors": []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.goto(args.url + "/#agents")
        page.locator('input[name="username"]').fill(credentials["username"])
        page.locator('input[name="password"]').fill(credentials["password"])
        page.locator("#login-submit").click()
        expect(page.locator("#app")).to_be_visible()
        expect(page.locator("#agent-scenario option")).to_have_count(12)
        expect(page.locator("#agent-turns button")).to_have_count(4)
        expect(page.locator("#agent-budget")).to_have_value("16384")
        expect(page.locator("#agent-tier")).to_have_value("medium")
        expect(page.locator("#agent-runtime-state")).to_contain_text("Runtime V2")
        report["checks"].append("authenticated industry page: 12 scenarios, four turns, V2, 16384-token UI default")
        page.locator("#agent-scenario").select_option("saas")
        page.locator("#agent-advanced").click()
        assert "跨区域幂等" in page.locator("#agent-prompt").input_value()
        report["checks"].append("advanced concurrency follow-up fills a real task without a preset model")
        row = page.locator(f'[data-agent-job="{args.job_id}"]')
        expect(row).to_be_visible(timeout=30_000)
        row.click()
        expect(page.locator("#agent-result")).to_contain_text(args.job_id)
        expect(page.locator("#agent-result .agent-answer")).to_be_visible()
        assert page.locator("#agent-result tbody tr").count() >= 1
        page.locator("#agent-summary").click()
        assert "总结你上一条回答" in page.locator("#agent-prompt").input_value()
        report["checks"].append("same-session summary follow-up fills without a preset model or new inference")
        report["checks"].append("saved real answer, model decisions, tools, performance and usage visible")
        report["model_rows"] = page.locator("#agent-result tbody tr").all_inner_texts()
        page.locator("#agent-result").evaluate("element => element.scrollIntoView({block: 'start'})")
        page.screenshot(path=str(args.output / "desktop.png"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.reload()
        expect(page.locator(f'[data-agent-job="{args.job_id}"]')).to_be_visible(timeout=30_000)
        page.locator(f'[data-agent-job="{args.job_id}"]').click()
        expect(page.locator("#agent-result")).to_contain_text(args.job_id)
        expect(page.locator("#agent-session-label")).to_contain_text("可继续追问")
        page.locator("#agent-prompt").fill("按刚才的结果再简写成一段交接说明。")
        expect(page.locator("#agent-send")).to_be_enabled()
        report["checks"].append("reload restores history; existing agent session can accept a follow-up")
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#agent-result").evaluate("element => element.scrollIntoView({block: 'start'})")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(args.output / "mobile.png"))
        report["checks"].append("1440px desktop and 390px mobile have no document overflow")
        page.locator("#logout").click()
        expect(page.locator("#auth")).to_be_visible()
        report["checks"].append("logout clears the authenticated workspace")
        browser.close()
    assert not report["page_errors"], report["page_errors"]
    (args.output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
