"""E2E accessibility: axe-core sweep.

Runs axe-core on the dashboard and asserts no `serious` or `critical`
violations. We tolerate `moderate` and `minor` for Phase 1.
"""
from __future__ import annotations

import time
import uuid

import pytest


pytestmark = [pytest.mark.a11y, pytest.mark.slow]


AXE_SCRIPT = r"""
async () => {
  const s = document.createElement('script');
  s.src = 'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.0/axe.min.js';
  document.head.appendChild(s);
  await new Promise((res, rej) => {
    s.onload = res;
    s.onerror = rej;
    setTimeout(rej, 5000);
  });
  return await window.axe.run(document, {
    resultTypes: ['violations'],
  });
}
"""


def test_no_serious_axe_violations_on_dashboard(browser, frontend_url):
    import httpx
    email = f"e2e+{uuid.uuid4().hex[:8]}@example.com"
    password = "E2E!pass1234"
    base = frontend_url.rsplit(":", 1)[0] + ":8000"
    httpx.post(f"{base}/api/auth/register",
               json={"email": email, "password": password, "name": "e2e"})
    r = httpx.post(f"{base}/api/auth/login-json",
                   json={"email": email, "password": password})
    token = r.json()["access_token"]

    context = browser.new_context()
    page = context.new_page()
    page.add_init_script(f"localStorage.setItem('athena.auth', '{token}')")
    page.goto(frontend_url)
    page.wait_for_load_state("networkidle")

    try:
        results = page.evaluate(AXE_SCRIPT)
    except Exception as e:
        pytest.skip(f"axe-core injection failed: {e}")
    violations = results.get("violations", [])
    serious = [v for v in violations if v.get("impact") in ("serious", "critical")]
    assert not serious, (
        "axe found serious/critical violations:\n"
        + "\n".join(f"  - {v['id']}: {v['description']}" for v in serious)
    )
    context.close()
