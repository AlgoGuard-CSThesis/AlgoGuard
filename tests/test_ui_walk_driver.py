"""The browser walk's driver (tests/integration/ui_walk_driver.py), checked without a browser."""

import importlib.util
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

RELEASING = ("The previous capture is still releasing its resources. Try again shortly.")


@pytest.fixture()
def driver(monkeypatch):
    """The driver module, loaded with a stand-in for Playwright (not installed here)."""
    stand_in = types.ModuleType("playwright.sync_api")
    stand_in.sync_playwright = None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", stand_in)
    path = Path(__file__).parent / "integration" / "ui_walk_driver.py"
    spec = importlib.util.spec_from_file_location("ui_walk_driver_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Reply:
    def __init__(self, status, body):
        self.status, self.body = status, body
        self.url = "http://127.0.0.1:5078/monitor/start"

    def text(self):
        return self.body


class Page:
    """Answers each press of Start with the next reply; a refusal is also logged, as the walk's
    response and console watchers log it."""

    def __init__(self, replies, problems):
        self.replies, self.problems, self.presses = list(replies), problems, 0

    @contextmanager
    def expect_response(self, matches):
        holder = types.SimpleNamespace(value=None)
        yield holder
        holder.value = self.replies.pop(0)
        assert matches(holder.value)

    def click(self, selector):
        assert selector == "#monitorStart"
        self.presses += 1
        reply = self.replies[0]
        if reply.status >= 400:
            self.problems.append({"kind": f"HTTP {reply.status}", "url": reply.url + "?x=1",
                                  "text": "CONFLICT"})
            self.problems.append({"kind": "console", "url": "http://127.0.0.1:5078/monitor",
                                  "text": "Failed to load resource: the server responded with a "
                                          f"status of {reply.status} (CONFLICT)"})

    def wait_for_timeout(self, milliseconds):
        assert milliseconds <= 1000


def test_start_is_pressed_again_while_the_previous_capture_is_releasing(driver):
    earlier = {"kind": "console", "url": "http://127.0.0.1:5078/", "text": "earlier error"}
    problems = [earlier]
    page = Page([Reply(409, RELEASING), Reply(409, RELEASING), Reply(200, "{}")], problems)

    assert driver.press_start(page, problems) == 200
    assert page.presses == 3
    assert problems == [earlier]  # the designed refusals are set aside, nothing else


def test_any_other_refusal_is_returned_and_stays_a_problem(driver):
    problems = []
    page = Page([Reply(409, "A monitoring session is already running. Stop it first.")], problems)

    assert driver.press_start(page, problems) == 409
    assert page.presses == 1
    assert [item["kind"] for item in problems] == ["HTTP 409", "console"]


def test_a_refusal_that_outlasts_the_wait_stays_a_problem(driver):
    problems = []
    page = Page([Reply(409, RELEASING)], problems)

    assert driver.press_start(page, problems, seconds=-1) == 409
    assert [item["kind"] for item in problems] == ["HTTP 409", "console"]
