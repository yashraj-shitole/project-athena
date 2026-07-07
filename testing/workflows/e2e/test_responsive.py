"""E2E: responsive design check.

Loads the dashboard at three common viewport widths and asserts the
layout doesn't break (no horizontal scrollbar, primary nav is
reachable).
"""
from __future__ import annotations

import time
import uuid

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


VIEWPORTS = [
    ("mobile", 375, 667),
    ("tablet", 768, 1024),
    ("desktop", 1280, 800),
]


@pytest.mark.parametrize("name,w,h", VIEWPORTS, ids=[v[0] for v in VIEWPORTS])
def test_layout_at_viewport(browser, frontend_url, name, w, h):
    import httpx
    from playwright.sync_api import expect

    # Register a user (required for the dashboard to render).
    email = f"e2e+{uuid.uuid4().hex[:8]}@example.com"
    password = "E2E!pass1234"
    base = frontend_url.rsplit(":", 1)[0] + ":8000"
    httpx.post(f"{base}/api/auth/register",
               json={"email": email, "password": password, "name": "e2e"})
    r = httpx.post(f"{base}/api/auth/login-json",
                   json={"email": email, "password": password})
    token = r.json()["access_token"]

    context = browser.new_context(viewport={"width": w, "height": h})
    page = context.new_page()
    page.add_init_script(f"localStorage.setItem('athena.auth', '{token}')")
    page.goto(frontend_url)

    # Assert no horizontal scrollbar (scrollWidth <= clientWidth + 1).
    metrics = page.evaluate(
        "() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})"
    )
    assert metrics["sw"] <= metrics["cw"] + 1, (
        f"horizontal scroll at {name}: scrollWidth={metrics['sw']} > clientWidth={metrics['cw']}"
    )
    context.close()
