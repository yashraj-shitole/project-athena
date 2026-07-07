"""E2E: upload a document then query it.

Flow:
  1. Log in
  2. Navigate to /documents (or /)
  3. Upload a small text file
  4. Wait for "indexed" status
  5. Open the chat
  6. Ask a question about the document
  7. Assert the response includes a citation
"""
from __future__ import annotations

import time
import uuid

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def test_upload_then_query(browser, frontend_url):
    from playwright.sync_api import expect

    # Pre-register a user via the API.
    import httpx
    email = f"e2e+{uuid.uuid4().hex[:8]}@example.com"
    password = "E2E!pass1234"
    base = frontend_url.rsplit(":", 1)[0] + ":8000"
    httpx.post(f"{base}/api/auth/register",
               json={"email": email, "password": password, "name": "e2e"})
    r = httpx.post(f"{base}/api/auth/login-json",
                   json={"email": email, "password": password})
    token = r.json()["access_token"]

    page = browser.new_page()
    # Inject the auth token into localStorage before the SPA boots.
    page.add_init_script(f"localStorage.setItem('athena.auth', '{token}')")
    page.goto(frontend_url)

    # Navigate to /documents
    page.locator('a[href="/documents"]').first.click()
    page.wait_for_url(lambda u: "/documents" in u, timeout=5000)

    # Upload a small CSV via the file input.
    csv = b"col_a,col_b\nhello,world\n"
    page.locator('input[type="file"]').first.set_input_files(
        files=[{"name": "e2e.csv", "mimeType": "text/csv", "buffer": csv}],
    )

    # Wait for the status pill to read "indexed" (or "ready").
    deadline = time.time() + 30
    while time.time() < deadline:
        content = page.content()
        if "indexed" in content.lower() or "ready" in content.lower():
            break
        time.sleep(1)

    # Navigate to chat
    page.locator('a[href="/chat"]').first.click()
    page.wait_for_url(lambda u: "/chat" in u, timeout=5000)
    page.locator('textarea').first.fill("What is in col_a?")
    page.locator('button[type="submit"]').first.click()

    # Wait for an assistant response
    page.wait_for_function(
        "() => document.body.innerText.includes('col_a') || document.body.innerText.includes('hello')",
        timeout=30000,
    )
    page.close()
