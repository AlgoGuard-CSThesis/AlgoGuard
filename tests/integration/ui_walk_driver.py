"""Stage 6D.4 browser walk, run by test_ui_walk.py with a Python that has Playwright.

    <python with playwright> ui_walk_driver.py <settings.json>

Visits every page as an analyst and as an administrator in headless Microsoft Edge, at a
desktop width (1366 px) and a phone width (390 px). Starts each traffic source from the Live
Monitor, submits a manual prediction, creates a report from alerts, previews retention and
creates an account. Records console errors, page errors, failed requests, error responses that
were not expected, and pages wider than the screen, and saves a screenshot of every page. Prints
a JSON summary. It imports nothing from AlgoGuard.
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import (  # pyright: ignore[reportMissingImports]
    sync_playwright,
)

VIEWPORTS = {"desktop": {"width": 1366, "height": 768}, "phone": {"width": 390, "height": 844}}


def main():
    settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    base, output = settings["base"], Path(settings["output"])
    output.mkdir(parents=True, exist_ok=True)
    pages, problems, sessions = [], [], []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        for viewport, size in VIEWPORTS.items():
            for role in ("analyst", "admin"):
                context = browser.new_context(viewport=size)
                page = context.new_page()
                expected: set[str] = set()
                watch(page, viewport, role, problems, expected)

                def visit(path, name, *, wait=0.8):
                    response = page.goto(base + path, wait_until="load")
                    time.sleep(wait)
                    record(page, viewport, role, name, response.status if response else None,
                           output, pages)

                # Sign-in, including a refused password, which answers with an error status.
                expected.add("/login")
                page.goto(base + "/login")
                page.fill("#email", settings[role]["email"])
                page.fill("#password", "Wrong!" + settings[role]["password"])
                page.click("button[type=submit]")
                page.wait_for_load_state("load")
                record(page, viewport, role, "login-refused", None, output, pages)
                page.fill("#email", settings[role]["email"])
                page.fill("#password", settings[role]["password"])
                page.click("button[type=submit]")
                page.wait_for_url(base + "/", timeout=60000)
                expected.discard("/login")
                record(page, viewport, role, "dashboard", 200, output, pages)

                if role == "analyst":
                    visit("/simulation", "predict")
                    page.click("form.simulation-form button[type=submit]")
                    page.wait_for_load_state("load")
                    record(page, viewport, role, "predict-result", None, output, pages)
                    visit("/monitor", "monitor")
                    for source, options in (
                        ("csv", {"#monitorSpeed": "max", "#monitorPersist": "attacks"}),
                        ("pcap", {"#monitorCapture": settings["pcap"],
                                  "#monitorSpeed": "fast", "#monitorPersist": "all"}),
                        ("live", {"#monitorPersist": "attacks"}),
                    ):
                        sessions.append(run_source(page, viewport, source, options, output,
                                                   pages))
                    visit("/alerts", "alerts")
                    boxes = page.locator("input[name=alert_id]")
                    for index in range(min(2, boxes.count())):
                        boxes.nth(index).check()
                    if boxes.count():
                        page.click("button[form=reportForm]")
                        page.wait_for_load_state("load")
                        record(page, viewport, role, "report-created", None, output, pages)
                    visit("/reports", "reports")
                    visit("/logs", "logs")
                    visit("/nodes", "nodes")
                else:
                    visit("/admins", "accounts")
                    if viewport == "desktop":
                        page.fill("#email", settings["new_account"]["email"])
                        page.fill("#username", settings["new_account"]["username"])
                        page.fill("#password", settings["new_account"]["password"])
                        page.fill("#confirm_password", settings["new_account"]["password"])
                        page.click("form.admin-form button[type=submit]")
                        page.wait_for_load_state("load")
                        record(page, viewport, role, "account-created", None, output, pages)
                    visit("/nodes", "nodes")
                    visit("/computers", "computers")
                    visit("/retention", "retention-preview")
                    visit("/alerts", "alerts")
                    visit("/reports", "reports")
                    report_link = page.locator("a[href^='/reports/']")
                    if report_link.count():
                        report_link.first.click()
                        page.wait_for_load_state("load")
                        record(page, viewport, role, "report-detail", None, output, pages)
                    visit("/logs", "logs")
                    visit("/monitor", "monitor")
                    visit("/simulation", "predict")
                expected.add("/no-such-page")
                visit("/no-such-page", "not-found")
                page.goto(base + "/", wait_until="load")  # the error page has no menu
                if page.locator("nav .navbar-toggler").is_visible():
                    page.click("nav .navbar-toggler")  # the phone layout folds the menu
                    page.wait_for_selector("#mainNav.show", timeout=5000)
                page.click("button[data-bs-target='#logoutConfirmModal']")
                page.click("button.btn-logout-confirm")
                page.wait_for_url("**/login**", timeout=30000)
                record(page, viewport, role, "logged-out", None, output, pages)
                context.close()
        browser.close()
    print(json.dumps({"pages": pages, "problems": problems, "sessions": sessions}))


def watch(page, viewport, role, problems, expected):
    where = {"viewport": viewport, "role": role}

    def console(message):
        # The browser also logs the deliberate error responses (a refused sign-in, an unknown
        # address); those are checked as responses below.
        if message.type == "error" and not any(page.url.split("?")[0].endswith(path)
                                               for path in expected):
            problems.append({**where, "kind": "console", "text": message.text,
                             "url": page.url})

    def failed(request):
        # A poll still in flight when the page navigates away is aborted by the browser.
        if request.failure == "net::ERR_ABORTED" and request.resource_type in ("fetch", "xhr"):
            return
        problems.append({**where, "kind": "request failed", "url": request.url,
                         "text": request.failure})

    def response(reply):
        if reply.status >= 400 and not any(reply.url.split("?")[0].endswith(item)
                                           for item in expected):
            problems.append({**where, "kind": f"HTTP {reply.status}", "url": reply.url,
                             "text": reply.status_text})

    page.on("console", console)
    page.on("pageerror", lambda error: problems.append(
        {**where, "kind": "page error", "text": str(error), "url": page.url}))
    page.on("requestfailed", failed)
    page.on("response", response)


def record(page, viewport, role, name, status, output, pages):
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth")
    # Where the overflow starts: elements past the right edge whose parent is not.
    wide = page.evaluate("""() => {
        const edge = document.documentElement.clientWidth + 1;
        const past = element => element.getBoundingClientRect().right > edge;
        return [...document.querySelectorAll('body *')]
            .filter(element => past(element) && !past(element.parentElement))
            .slice(0, 5)
            .map(element => element.tagName.toLowerCase() + '.' + [...element.classList].join('.')
                 + ' ' + Math.round(element.getBoundingClientRect().width) + 'px');
    }""") if overflow > 0 else []
    shot = output / f"{viewport}-{role}-{name}.jpg"
    page.screenshot(path=str(shot), type="jpeg", quality=55, full_page=True)
    pages.append({"viewport": viewport, "role": role, "page": name, "status": status,
                  "url": page.url, "title": page.title(), "overflow_px": overflow,
                  "overflowing_elements": wide, "screenshot": shot.name})


def wait_state(page, accept, seconds):
    """The Live Monitor's state text once ``accept`` takes it, or None after ``seconds``."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = page.locator("#monitorState").inner_text().strip().lower()
        if accept(state):
            return state
        time.sleep(0.25)
    return None


def run_source(page, viewport, source, options, output, pages):
    page.select_option("#monitorSource", source)
    for selector, value in options.items():
        page.select_option(selector, value)
    page.click("#monitorStart")
    began = time.monotonic()
    # Polled from here: the page's Content Security Policy rightly refuses the string
    # evaluation that Playwright's wait_for_function relies on.
    if wait_state(page, lambda state: state != "idle", 30) is None:
        record(page, viewport, "analyst", f"monitor-{source}-not-started", None, output, pages)
        return {"viewport": viewport, "source": source, "feed_rows": 0, "state": "not started",
                "message": page.locator("#monitorMessage").inner_text(), "seconds": 30.0,
                "score_band_shown": False}
    seconds = 25 if source == "live" else 12
    time.sleep(seconds)
    rows = page.locator("#feedBody tr").count()
    bands = page.locator("#feedBody").inner_text()
    record(page, viewport, "analyst", f"monitor-{source}-running", None, output, pages)
    if not page.locator("#monitorStop").is_disabled():
        page.click("#monitorStop")
    state = wait_state(page, lambda state: any(
        word in state for word in ("stopped", "completed", "error")), 60) or "did not end"
    message = page.locator("#monitorMessage").inner_text()
    record(page, viewport, "analyst", f"monitor-{source}-ended", None, output, pages)
    return {"viewport": viewport, "source": source, "feed_rows": rows, "state": state,
            "message": message, "seconds": round(time.monotonic() - began, 1),
            "score_band_shown": any(band in bands for band in ("Low", "Medium", "High",
                                                                "Very high"))}


if __name__ == "__main__":
    main()
