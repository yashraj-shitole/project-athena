"""Performance: concurrent chat users (locust).

This is the locustfile used by the perf suite. Run directly:

    locust -f testing/workflows/performance/locustfile.py \\
           --host=http://localhost:8000

Or in headless mode (used by CI):

    locust -f testing/workflows/performance/locustfile.py \\
           --host=http://localhost:8000 --headless -u 50 -r 10 -t 60s \\
           --csv=testing/reports/locust.csv

The CI gate is "p95 < 10s across 50 concurrent users for 60s".
"""
from __future__ import annotations

import os
import random

from locust import HttpUser, task, between


class AthenaUser(HttpUser):
    """Simulate a single logged-in Athena user."""

    # Pause between tasks.
    wait_time = between(1, 3)

    def on_start(self):
        # Register + log in.
        import uuid
        email = f"locust+{uuid.uuid4().hex[:8]}@example.com"
        password = "Locust!pass1"
        r = self.client.post(
            "/api/auth/register",
            json={"email": email, "password": password, "name": "locust"},
        )
        r = self.client.post(
            "/api/auth/login-json",
            json={"email": email, "password": password},
        )
        if r.status_code != 200:
            # If register failed (email in use), try login-only.
            r = self.client.post(
                "/api/auth/login-json",
                json={"email": email, "password": password},
            )
        self.token = r.json().get("access_token", "")
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

    @task(3)
    def chat_simple(self):
        if not self.headers:
            return
        self.client.post(
            "/api/chat",
            json={"message": "What is the capital of France?",
                  "conversation_id": None},
            headers=self.headers,
            timeout=60.0,
        )

    @task(1)
    def list_documents(self):
        if not self.headers:
            return
        self.client.get("/api/documents", headers=self.headers, timeout=10.0)

    @task(1)
    def list_conversations(self):
        if not self.headers:
            return
        self.client.get("/api/chat/conversations", headers=self.headers, timeout=10.0)

    @task(1)
    def health(self):
        self.client.get("/health", timeout=5.0)
