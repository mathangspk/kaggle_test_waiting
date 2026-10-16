"""Read-only reproduction of the production sampling path on the live panel.

Prints redacted row structure only (no cookies, bodies or full text). Never
navigates a status link, editor or run control.
"""
import asyncio
import hashlib
import json
import re
from urllib.parse import urlsplit

from playwright.async_api import async_playwright
from discovery_visit import identity_hint, verify_profile, collect_panel, ROW_SAMPLE_JS, observation


def safe(t):
    return re.sub(r"[A-Za-z0-9_-]{6,}", "<tok>", (t or ""))[:60]


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(storage_state="/private/thangma/storage-state.json")
            await ctx.route("**/code/**", lambda route: route.abort())
            page = await ctx.new_page()
            async with page.expect_response(lambda r: r.url.endswith("/users.UsersService/GetCurrentUser"), timeout=20000) as response:
                await page.goto("https://www.kaggle.com/", wait_until="domcontentloaded", timeout=20000)
            r = await response.value
            assert r.status == 200
            await verify_profile(page, identity_hint(await r.json()))
            await page.get_by_role("button", name=re.compile("View Active Events|Open Active Events")).click(timeout=5000)
            panel = page.get_by_role("tooltip")
            await panel.wait_for(timeout=5000)
            await page.wait_for_timeout(9000)

            raw = await panel.get_by_role("link", name=re.compile(r"^Status for .*Click to open")).evaluate_all(ROW_SAMPLE_JS)
            summary = []
            for row in raw:
                text = row.get("text", "")
                summary.append({"structured": bool(row.get("structured")),
                                "href_present": bool(row.get("href")),
                                "interactive": bool(row.get("interactive")),
                                "status": row.get("status"),
                                "title_redacted": safe(row.get("title")),
                                "row_versions": re.findall(r"\bVersion #(\d+)\b", text),
                                "has_interactive_label": "Interactive Session" in text,
                                "text_redacted": safe(text)})
            panel_text_versions = re.findall(r"\bVersion #(\d+)\b", await panel.inner_text())

            result = {"raw_rows": summary, "panel_text_versions": panel_text_versions}
            try:
                samples = await collect_panel(panel, page.wait_for_timeout)
                result["collect_panel_ok"] = True
                result["samples"] = [{"structured": bool(s.get("structured")),
                                      "href_present": bool(s.get("href")),
                                      "title_redacted": safe(s.get("title")),
                                      "interactive": bool(s.get("interactive")),
                                      "status": s.get("status")} for s in samples]
                try:
                    obs = [observation(s, 1000, i) for i, s in enumerate(samples)]
                    result["observations"] = [{"identity_kind": o["identity"].split(":")[0],
                                               "run_type": o["run_type"], "state": o["state"],
                                               "provider_state": o["provider_state"]} for o in obs]
                except Exception as exc:
                    result["observation_error"] = type(exc).__name__
            except Exception as exc:
                result["collect_panel_ok"] = False
                result["collect_panel_error"] = str(exc)[:60]
            print(json.dumps(result, sort_keys=True))
        finally:
            await browser.close()


asyncio.run(main())
