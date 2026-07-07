"""Post a CI summary to the PR.

Reads the coverage.xml artifact and the latest eval JSONL, formats
a markdown summary, and posts it as a comment on the PR.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path


def _gh_api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    """Tiny GitHub API client (no extra deps)."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("GH_TOKEN not set; skipping PR comment", file=sys.stderr)
        sys.exit(0)
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json" if body else "application/json",
            "User-Agent": "athena-ci-bot",
        },
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def main() -> int:
    # 1. Find the PR associated with this workflow run.
    run_id = os.environ.get("RUN_ID", "")
    if not run_id:
        print("RUN_ID env var not set", file=sys.stderr)
        return 1
    run = _gh_api(f"/actions/runs/{run_id}")
    pr_list = run.get("pull_requests") or []
    if not pr_list:
        print("No PR associated with this run", file=sys.stderr)
        return 0
    pr = pr_list[0]
    repo = run["repository"]["full_name"]
    pr_number = pr["number"]

    # 2. Build the comment body.
    cov = _read_coverage()
    eval_summary = _read_eval_summary()
    body = _format_comment(cov, eval_summary)

    # 3. Post (or update) the comment.
    existing = _find_existing_comment(repo, pr_number, marker="<!-- athena-ci-bot -->")
    if existing:
        _gh_api(
            f"/repos/{repo}/issues/comments/{existing['id']}",
            method="PATCH",
            body={"body": body},
        )
        print(f"Updated comment {existing['id']}")
    else:
        _gh_api(
            f"/repos/{repo}/issues/{pr_number}/comments",
            method="POST",
            body={"body": body},
        )
        print(f"Posted new comment to PR #{pr_number}")
    return 0


def _read_coverage() -> dict | None:
    cov_path = Path("testing/coverage/coverage.xml")
    if not cov_path.exists():
        return None
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(cov_path).getroot()
        return {
            "line_rate": float(root.get("line-rate", 0)),
            "branch_rate": float(root.get("branch-rate", 0)),
        }
    except Exception as e:
        print(f"coverage parse error: {e}", file=sys.stderr)
        return None


def _read_eval_summary() -> dict | None:
    reports = Path("testing/llm_evals/reports")
    if not reports.exists():
        return None
    jsonls = sorted(reports.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not jsonls:
        return None
    latest = jsonls[0]
    n = 0
    p = 0
    for line in latest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        n += 1
        if rec.get("passed"):
            p += 1
    return {"total": n, "passed": p, "rate": p / n if n else 0.0}


def _format_comment(cov: dict | None, evals: dict | None) -> str:
    lines = ["<!-- athena-ci-bot -->", "## Athena CI summary", ""]
    if cov:
        lines.append(f"- **Coverage:** lines {cov['line_rate']*100:.1f}% / branches {cov['branch_rate']*100:.1f}%")
    if evals:
        lines.append(f"- **LLM evals:** {evals['passed']} / {evals['total']} pass ({evals['rate']*100:.1f}%)")
    if not cov and not evals:
        lines.append("No coverage or eval reports found in this run.")
    return "\n".join(lines)


def _find_existing_comment(repo: str, pr_number: int, marker: str) -> dict | None:
    out = _gh_api(f"/repos/{repo}/issues/{pr_number}/comments?per_page=100")
    for c in out:
        if marker in (c.get("body") or ""):
            return c
    return None


if __name__ == "__main__":
    sys.exit(main())
