"""Verify Bedrock classified routing UI in replay; never invoke a decision/target model."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8037")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Use a new evidence directory")
    args.output.mkdir(parents=True)
    credentials = json.loads(args.credentials.read_text())
    errors, calls, forbidden = [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def guard(route):
            if route.request.url.endswith("/api/routing/auto"):
                body = route.request.post_data_json
                assert body["mode"] == "replay"
                calls.append(body)
                route.continue_()
            else:
                forbidden.append(route.request.url)
                route.fulfill(
                    status=409, json={"detail": "No GPU mutation or target calls in UI replay"}
                )

        for path in (
            "api/routing/auto",
            "api/routing/auto/execute",
            "api/gpu/start",
            "api/gpu/stop",
        ):
            page.route("**/" + path, guard)
        page.goto(args.url + "/#routing")
        page.locator("#login-username").fill(credentials["username"])
        page.locator("#login-password").fill(credentials["password"])
        page.locator("#login-submit").click()
        expect(page.locator(".rt-case")).to_have_count(4)
        expect(page.locator("[data-rt-strategy]")).to_have_value("classified")
        expect(page.locator("[data-rt-routers]")).to_have_value("bedrock")
        page.locator("[data-rt-mode]").select_option("replay")

        def export():
            with page.expect_download() as download:
                page.locator("[data-rt-export]").click()
            return json.loads(Path(download.value.path()).read_text())

        page.locator("[data-rt-all]").click()
        expect(page.locator("[data-rt-start]")).to_be_enabled(timeout=60000)
        data = export()
        assert len(calls) == 4
        catalog = page.request.get(args.url + "/api/routing/catalog").json()
        ids = {m["id"] for m in catalog["models"]}
        for case in data["cases"].values():
            side = case["sides"]["bedrock"]
            assert not side["error"]
            flow, model = side["steps"]
            assert flow["assessment_count"] == 7
            assert model["selected_model"]["id"] in ids
            assert model["model"] is None and model["route_token"] is None
            assert model["selection"]["evidence_mode"] == "benchmark_proxy"
        (args.output / "bedrock-replay.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2)
        )
        page.locator('[data-rt-case="code"]').click()
        expect(page.locator(".rt-side")).to_have_count(1)
        page.screenshot(path=str(args.output / "desktop.png"), full_page=True)
        # A claimed quality requirement cannot silently fall back to benchmark scores.
        page.locator('[data-rt-preference="min_success_rate"]').fill("0.9")
        page.locator('[data-rt-preference="min_success_rate"]').blur()
        page.locator("[data-rt-start]").click()
        expect(page.locator("[data-rt-start]")).to_be_enabled(timeout=30000)
        side = export()["cases"]["code"]["sides"]["bedrock"]
        assert side["error"] and side["steps"][-1]["selected_model"] is None
        expect(page.locator("[data-rt-execute]")).to_have_count(0)
        page.locator('[data-rt-preference="min_success_rate"]').fill("")
        page.locator('[data-rt-preference="min_success_rate"]').blur()
        # Same policy pipeline also works with the already available local classifier group.
        page.locator("[data-rt-routers]").select_option("local")
        page.locator("[data-rt-start]").click()
        expect(page.locator("[data-rt-start]")).to_be_enabled(timeout=30000)
        expect(page.locator(".rt-side")).to_have_count(3)
        local = export()["cases"]["code"]
        assert all(not s["error"] for s in local["sides"].values())
        page.locator("#rt-catalog > summary").click()
        page.locator("[data-rt-category]").select_option("embedding")
        expected = catalog["classified_routing"]["categories"]["embedding"]["available_model_count"]
        expect(page.locator("#rt-catalog-table tbody tr")).to_have_count(expected)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        page.screenshot(path=str(args.output / "mobile.png"), full_page=True)
        assert not errors and not forbidden, (errors, forbidden)
        (args.output / "verification.json").write_text(
            json.dumps(
                {
                    "mode": "replay",
                    "model_calls": 0,
                    "bedrock_cases": 4,
                    "local_classifiers": 3,
                    "no_route_requirement_checked": True,
                    "category_filter_checked": True,
                    "mobile_overflow": False,
                    "browser_errors": errors,
                    "forbidden_calls": forbidden,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        browser.close()
    print(json.dumps({"passed": True, "output": str(args.output)}))


if __name__ == "__main__":
    main()
