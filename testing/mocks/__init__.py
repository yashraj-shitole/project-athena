"""Stubs for HTTP mocks. Importing the package forces `httpx` to be
a dev dep, which is fine — the conftest in `testing/conftest.py`
already requires httpx.
"""
from __future__ import annotations

import httpx  # noqa: F401
