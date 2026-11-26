"""Live AgentCore checks: select before the first target and show selection during generation."""

import argparse
import hashlib
import json
import re
from pathlib import Path

import boto3
from playwright.sync_api import expect, sync_playwright

CASES = [
    ("finance-sum", "finance", "查询结算记录，用计算工具计算去重前的结算净额。"
     '金额单位为分，最后只输出JSON：{"raw_net_cents":整数}。', {"raw_net_cents": 5000}),
    ("finance-code", None, "现在编写Python函数，按event去重结算记录后求金额合计。"
     "同event金额冲突必须报错，不能修改输入，金额只接受整数分。"
     "给出重复记录、金额冲突、负数退款、空输入四个测试。不要查询新数据。", None),
    ("energy-shift", "energy", "查询负荷、柔性资源和业务规则，计算移峰前电费、移峰后电费、"
     "净节省及额外耗电。"
     '最后只输出JSON，字段为before_yuan、after_yuan、saving_yuan、extra_kwh，值为数字。',
     {"before_yuan": 4000, "after_yuan": 3734.5, "saving_yuan": 265.5, "extra_kwh": 15}),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    credentials = json.loads(boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId="DeciderModelRouter/admin-credentials")["SecretString"])
    url = "https://d3eipg2g3l3an3.cloudfront.net/"
    report = {"scope": "three focused live requests; not a new overall quality score",
              "cases": [], "browser_errors": []}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1050},
                                      extra_http_headers={"X-Decider-Client": "web"})
        assert context.request.post(url + "api/auth/login", data=credentials).ok
        page = context.new_page()
        page.on("pageerror", lambda e: report["browser_errors"].append(str(e)))
        page.goto(url + "#agents")
        expect(page.locator("#agent-scenario option")).to_have_count(12, timeout=30000)
        for cid, scenario, prompt, expected in CASES:
            if scenario:
                page.locator("#agent-scenario").select_option(scenario)
            page.locator("#agent-prompt").fill(prompt)
            captured = []

            def capture(response, captured=captured, cid=cid):
                if re.search(r"/api/gateway/agent/jobs/[a-f0-9]{24}$", response.url):
                    if response.ok:
                        job = response.json()
                        captured.append(job)
                        (args.output / (cid + ".json")).write_text(json.dumps(job, ensure_ascii=False))

            page.on("response", capture)
            page.locator("#agent-send").click()
            # Wait through actual UI polling, never fabricate running-phase data.
            page.wait_for_function(
                """() => !document.querySelector('#agent-send').disabled ||
                document.querySelector('#agent-result').textContent.includes('目标模型生成中')""",
                timeout=660000,
            )
            visible_early = "目标模型生成中" in page.locator("#agent-result").inner_text()
            if visible_early:
                page.locator("#agent-result").screenshot(path=str(args.output / (cid + "-generating.png")))
            expect(page.locator("#agent-send")).to_be_enabled(timeout=660000)
            page.remove_listener("response", capture)
            assert captured, cid
            final = captured[-1]
            assert final["status"] == "completed", final.get("error")
            assert final["platform_version"] == "V2" and final["model"] == "model-router"
            assert final["steps"]
            for step in final["steps"]:
                assert step["selected_at"] <= step["target_started_at"] <= step["target_finished_at"]
                assert step["decision_mode"] == "two_stage" and step["model_decision_calls"] == 2
                assert step["routing_context_version"] == "agent-initial-decision-v1"
            early = [j for j in captured if any(
                s.get("phase") == "executing" and s.get("model_id") and not s.get("target_finished_at")
                for s in j["steps"])]
            (args.output / (cid + "-progress.json")).write_text(json.dumps(early, ensure_ascii=False))
            content_pass = None
            if expected is not None:
                try:
                    content_pass = json.loads(final["answer"]) == expected
                except (ValueError, TypeError):
                    content_pass = False
            report["cases"].append({
                "id": cid, "job_id": final["id"], "session_id": final["session_id"],
                "complete": True, "calls": len(final["steps"]),
                "selected_before_every_target": True, "observed_while_generating": bool(early),
                "visible_while_generating": visible_early,
                "models": [s["model_id"] for s in final["steps"]],
                "decision_ms": [s["model_decision_ms"] for s in final["steps"]],
                "content_pass": content_pass,
                "content_method": "exact JSON oracle" if expected else "not scored; generated code not executed",
            })
            page.locator("#agent-result").screenshot(path=str(args.output / (cid + ".png")))
            (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(cid, report["cases"][-1], flush=True)
        assert any(c["observed_while_generating"] for c in report["cases"])
        assert any(c["visible_while_generating"] for c in report["cases"])
        hashes = {}
        for name in ("index.html", "agents.js"):
            response = context.request.get(url + "gateway/" + name)
            assert response.ok
            digest = hashlib.sha256(response.body()).hexdigest()
            assert digest == hashlib.sha256((Path("frontend/gateway") / name).read_bytes()).hexdigest()
            hashes[name] = digest
        report["assets"] = hashes
        assert not report["browser_errors"]
        context.request.post(url + "api/auth/logout", data={})
        browser.close()
    (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
