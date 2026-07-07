#!/usr/bin/env python3
"""Run every eval scenario and write HTML/JSON/MD/CSV reports.

Usage:
    python run_eval.py                  # run everything
    python run_eval.py --dataset general_qa
    python run_eval.py --judge openai   # gold-standard judge
    python run_eval.py --baseline       # save current run as the new baseline
    python run_eval.py --check          # fail on regression vs the saved baseline

This is the canonical "run everything" entry point. The pytest
scenarios in `llm-evals/scenarios/` are the developer-loop entry
point; this script is the CI / nightly entry point.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Make `llm_evals` importable from any CWD.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from llm_evals.eval.baselines import (  # noqa: E402
    compare_to_baseline,
    load_baseline,
    save_baseline,
)
from llm_evals.eval.judges import get_judge  # noqa: E402
from llm_evals.eval.reports import write_all  # noqa: E402
from llm_evals.eval.runners import EvalRunner, set_runner  # noqa: E402


def _discover_scenarios(dataset: str | None) -> list:
    """Find every `@scenario`-decorated function in `llm_evals/scenarios/`.

    Returns a list of `(module_name, fn)` pairs.
    """
    import importlib
    import pkgutil

    import llm_evals.scenarios as scenarios_pkg

    out = []
    for mod_info in pkgutil.iter_modules(scenarios_pkg.__path__):
        if not mod_info.name.startswith("test_"):
            continue
        m = importlib.import_module(f"llm_evals.scenarios.{mod_info.name}")
        for name in dir(m):
            fn = getattr(m, name, None)
            if fn is None or not callable(fn):
                continue
            sc = getattr(fn, "__athena_scenario__", None)
            if sc is None:
                continue
            if dataset and sc.dataset != dataset:
                continue
            out.append((mod_info.name, fn, sc))
    return out


async def _run_all(args: argparse.Namespace) -> int:
    judge = get_judge(choice=args.judge)
    runner = EvalRunner(judge=judge)
    set_runner(runner)
    scenarios = _discover_scenarios(args.dataset)
    if not scenarios:
        print(f"No scenarios discovered (dataset={args.dataset!r})", file=sys.stderr)
        return 2
    print(f"Discovered {len(scenarios)} scenario(s); running...")

    results: dict[str, float] = {}
    failures: list[str] = []
    for mod, fn, sc in scenarios:
        try:
            er = await runner.run(sc, {"question": "", "scenario_name": sc.name})
            score = (
                sum(s.score for s in er.scores.values()) / max(1, len(er.scores))
                if er.scores
                else 1.0
            )
            results[sc.name] = score
            tag = "PASS" if er.passed else "FAIL"
            print(f"  [{tag}] {mod}::{sc.name}  score={score:.2f}")
            if not er.passed:
                failures.append(sc.name)
        except Exception as e:
            print(f"  [ERR]  {mod}::{sc.name}  {type(e).__name__}: {e}", file=sys.stderr)
            failures.append(sc.name)

    # Reports
    paths = write_all(runner.stream_path)
    print("\nReports:")
    for kind, p in paths.items():
        print(f"  {kind}: {p}")

    # Baseline management
    if args.baseline:
        bpath = save_baseline(
            results, judge_name=judge.name,
            model=getattr(judge, "model", ""),
        )
        print(f"\nSaved baseline: {bpath}")
    if args.check:
        cmp = compare_to_baseline(results, judge_name=judge.name)
        print(f"\nBaseline comparison: {'PASS' if cmp.passed else 'FAIL'}")
        if cmp.regressions:
            print("Regressions:")
            for r in cmp.regressions:
                print(f"  - {r.scenario}: baseline={r.baseline_score:.2f} current={r.current_score:.2f} delta={r.delta:.2f}")
        if not cmp.passed:
            return 1

    if failures:
        print(f"\n{len(failures)} scenario(s) failed.")
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", help="Run only this dataset")
    p.add_argument("--judge", choices=["ollama", "openai", "heuristic"],
                   default="ollama", help="LLM judge (default: ollama)")
    p.add_argument("--baseline", action="store_true",
                   help="Save the current run as the new baseline")
    p.add_argument("--check", action="store_true",
                   help="Fail on regression vs the saved baseline")
    args = p.parse_args()
    return asyncio.run(_run_all(args))


if __name__ == "__main__":
    sys.exit(main())
