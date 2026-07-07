#!/usr/bin/env python3
"""Convert a run JSONL into all four report formats (HTML, JSON, MD, CSV).

Usage:
    python export_report.py path/to/run.jsonl
    python export_report.py path/to/run.jsonl --out-dir path/to/reports
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from llm_evals.eval.reports import write_all  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("stream", help="Path to a run JSONL file")
    p.add_argument("--out-dir", help="Output directory (default: same as stream)")
    args = p.parse_args()
    stream = Path(args.stream)
    if not stream.exists():
        print(f"No such file: {stream}", file=sys.stderr)
        return 2
    out = Path(args.out_dir) if args.out_dir else stream.parent
    paths = write_all(stream, reports_dir=out)
    for kind, p in paths.items():
        print(f"{kind}: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
