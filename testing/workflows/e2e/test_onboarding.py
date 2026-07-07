"""E2E: user onboarding via the browser.

1. Visit /login
2. Click "Register" (or navigate to /register)
3. Fill the form
4. Land on the dashboard

This requires a running frontend (Vite dev server, or the nginx
proxy on :8080). The base URL is configurable via --base-url.
"""
from __future__ import annotations

import time
import uuid

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture
def frontend_url(request):
    return (request.config.getoption("--frontend-url")
            if request.config.getoption("--frontend-url", default=False)
            else "http://localhost:5173")


@pytest.fixture
def browser():
    """Lazily import playwright; if it's not installed, the test is
    skipped with a clear message."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed; pip install playwright && playwright install")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


def test_register_then_land_on_dashboard(browser, frontend_url):
    from playwright.sync_api import expect

    page = browser.new_page()
    page.goto(f"{frontend_url}/register")

    email = f"e2e+{uuid.uuid4().hex[:8]}@example.com"
    password = "E2E!pass1234"

    page.locator('input[name="email"]').fill(email)
    page.locator('input[name="password"]').fill(password)
    page.locator('input[name="name"]').fill("e2e")
    page.locator('button[type="submit"]').click()

    # Should land on the dashboard within 5s.
    page.wait_for_url(lambda u: "/login" not in u, timeout=5000)
    # The dashboard should show the user's email (or a generic greeting).
    body = page.content()
    assert email in body or "Dashboard" in body or "Chat" in body
    page.close()


def test_login_with_existing_account(browser, frontend_url):
    """Pre-condition: a user exists. We register one via the API to
    avoid coupling to UI state."""
    import httpx
    from playwright.sync_api import expect

    email = f"e2e+{uuid.uuid4().hex[:8]}@example.com"
    password = "E2E!pass1234"
    base = frontend_url.rsplit(":", 1)[0] + ":8000"  # API on :8000
    httpx.post(f"{base}/api/auth/register",
               json={"email": email, "password": password, "name": "e2e"})

    page = browser.new_page()
    page.goto(f"{frontend_url}/login")
    page.locator('input[name="email"]').fill(email)
    page.locator('input[name="password"]').fill(password)
    page.locator('button[type="submit"]').click()
    page.wait_for_url(lambda u: "/login" not in u, timeout=5000)
    page.close()
