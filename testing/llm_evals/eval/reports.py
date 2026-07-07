"""Report writers: HTML, JSON, Markdown, CSV.

Each writer takes the run's JSONL stream and produces one output file
under `llm-evals/reports/`. The writers are deliberately simple —
no template engines, no JS charts. The HTML is a self-contained file
with inline CSS, the Markdown is a 2-column table, the CSV is a flat
row-per-scenario.
"""
from __future__ import annotations

import csv
import html
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _load_stream(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _aggregate(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = list(records)
    if not records:
        return {"count": 0, "pass_rate": 0.0, "mean_latency_ms": 0.0}
    passed = sum(1 for r in records if r.get("passed"))
    latencies = [r.get("latency_ms", 0.0) for r in records]
    return {
        "count": len(records),
        "passed": passed,
        "pass_rate": passed / len(records),
        "mean_latency_ms": statistics.fmean(latencies) if latencies else 0.0,
    }


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def write_html(stream_path: Path, out_path: Path) -> Path:
    records = _load_stream(stream_path)
    agg = _aggregate(records)
    by_dataset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        by_dataset[r.get("dataset", "unknown")].append(r)

    rows = []
    for r in records:
        score_summary = ", ".join(
            f"{n}={s.get('score', 0.0):.2f}"
            for n, s in (r.get("scores") or {}).items()
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(r.get('scenario', ''))}</td>"
            f"<td>{html.escape(r.get('dataset', ''))}</td>"
            f"<td class='{'pass' if r.get('passed') else 'fail'}'>"
            f"{'PASS' if r.get('passed') else 'FAIL'}</td>"
            f"<td>{html.escape(score_summary)}</td>"
            f"<td>{r.get('latency_ms', 0.0):.0f} ms</td>"
            f"<td>{html.escape((r.get('error') or '')[:200])}</td>"
            "</tr>"
        )

    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Athena eval report — {html.escape(stream_path.stem)}</title>
  <style>
    body {{ font-family: -apple-system, system-ui, sans-serif; margin: 24px; color: #1f2937; }}
    h1 {{ margin-bottom: 4px; }}
    .meta {{ color: #6b7280; font-size: 0.9em; margin-bottom: 16px; }}
    .summary {{ background: #f3f4f6; padding: 12px 16px; border-radius: 6px; margin-bottom: 24px; }}
    .summary strong {{ font-size: 1.4em; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.92em; }}
    th, td {{ padding: 8px 12px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: top; }}
    th {{ background: #f9fafb; font-weight: 600; }}
    .pass {{ color: #047857; font-weight: 600; }}
    .fail {{ color: #b91c1c; font-weight: 600; }}
  </style>
</head>
<body>
  <h1>Athena eval report</h1>
  <div class="meta">Run <code>{html.escape(stream_path.stem)}</code> — {agg['count']} scenarios</div>
  <div class="summary">
    <div><strong>{agg['pass_rate']*100:.1f}%</strong> pass rate ({agg['passed']} / {agg['count']})</div>
    <div>Mean latency: <strong>{agg['mean_latency_ms']:.0f} ms</strong></div>
  </div>
  <table>
    <thead><tr><th>Scenario</th><th>Dataset</th><th>Result</th><th>Scores</th><th>Latency</th><th>Error</th></tr></thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""
    out_path.write_text(body, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def write_json(stream_path: Path, out_path: Path) -> Path:
    records = _load_stream(stream_path)
    payload = {
        "run_id": stream_path.stem,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "aggregate": _aggregate(records),
        "records": records,
    }
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def write_markdown(stream_path: Path, out_path: Path) -> Path:
    records = _load_stream(stream_path)
    agg = _aggregate(records)
    lines = [
        f"# Athena eval report — `{stream_path.stem}`",
        "",
        f"- **Pass rate:** {agg['pass_rate']*100:.1f}% ({agg['passed']} / {agg['count']})",
        f"- **Mean latency:** {agg['mean_latency_ms']:.0f} ms",
        "",
        "| Scenario | Dataset | Result | Scores | Latency |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in records:
        scores = ", ".join(
            f"{n}={s.get('score', 0.0):.2f}"
            for n, s in (r.get("scores") or {}).items()
        )
        lines.append(
            f"| {r.get('scenario', '')} | {r.get('dataset', '')} "
            f"| {'PASS' if r.get('passed') else 'FAIL'} | {scores} "
            f"| {r.get('latency_ms', 0.0):.0f} ms |"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def write_csv(stream_path: Path, out_path: Path) -> Path:
    records = _load_stream(stream_path)
    fieldnames = [
        "scenario", "dataset", "passed", "latency_ms", "model",
        "connector_id", "error", "scores",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in records:
            w.writerow({
                "scenario": r.get("scenario", ""),
                "dataset": r.get("dataset", ""),
                "passed": r.get("passed", False),
                "latency_ms": round(r.get("latency_ms", 0.0), 1),
                "model": r.get("model") or "",
                "connector_id": r.get("connector_id") or "",
                "error": (r.get("error") or "")[:500],
                "scores": "; ".join(
                    f"{n}={s.get('score', 0.0):.2f}"
                    for n, s in (r.get("scores") or {}).items()
                ),
            })
    return out_path


# ---------------------------------------------------------------------------
# All four
# ---------------------------------------------------------------------------

def write_all(stream_path: Path, reports_dir: Path | None = None) -> dict[str, Path]:
    reports_dir = reports_dir or stream_path.parent
    stem = stream_path.stem
    return {
        "html": write_html(stream_path, reports_dir / f"{stem}.html"),
        "json": write_json(stream_path, reports_dir / f"{stem}.json"),
        "md": write_markdown(stream_path, reports_dir / f"{stem}.md"),
        "csv": write_csv(stream_path, reports_dir / f"{stem}.csv"),
    }
