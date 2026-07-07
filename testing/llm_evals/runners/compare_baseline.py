#!/usr/bin/env python3
"""Compare a run to the saved baseline; exit 1 on regression.

Usage:
    python compare_baseline.py --judge ollama
    python compare_baseline.py --current path/to/run.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from llm_evals.eval.baselines import (  # noqa: E402
    compare_to_baseline,
    load_baseline,
)


def _scores_from_jsonl(path: Path) -> dict[str, float]:
    """Aggregate the per-scenario mean score from a run's JSONL stream."""
    out: dict[str, list[float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        scores = rec.get("scores") or {}
        if not scores:
            out.setdefault(rec.get("scenario", "?"), []).append(0.0)
            continue
        mean = sum(s.get("score", 0.0) for s in scores.values()) / len(scores)
        out.setdefault(rec.get("scenario", "?"), []).append(mean)
    return {k: sum(v) / len(v) for k, v in out.items()}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--current", required=True, help="Path to a run JSONL file")
    p.add_argument("--judge", default="ollama", help="Judge name whose baseline to load")
    args = p.parse_args()
    current = _scores_from_jsonl(Path(args.current))
    baseline = load_baseline(args.judge)
    if not baseline:
        print(f"No baseline found for judge={args.judge!r}", file=sys.stderr)
        return 2
    cmp = compare_to_baseline(current, baseline, judge_name=args.judge)
    print(json.dumps(cmp.to_dict(), indent=2, sort_keys=True))
    return 0 if cmp.passed else 1


if __name__ == "__main__":
    sys.exit(main())
