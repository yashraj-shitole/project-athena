#!/usr/bin/env python3
"""Run a single eval scenario by name.

Usage:
    python run_scenario.py test_capital_of_france
    python run_scenario.py test_capital_of_france --judge heuristic
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import llm_evals.scenarios as scenarios_pkg  # noqa: E402
from llm_evals.eval.judges import get_judge  # noqa: E402
from llm_evals.eval.reports import write_all  # noqa: E402
from llm_evals.eval.runners import EvalRunner, set_runner  # noqa: E402


def _find_scenario(name: str):
    import importlib
    import pkgutil
    for mod_info in pkgutil.iter_modules(scenarios_pkg.__path__):
        if not mod_info.name.startswith("test_"):
            continue
        m = importlib.import_module(f"llm_evals.scenarios.{mod_info.name}")
        fn = getattr(m, name, None)
        if fn is None:
            continue
        sc = getattr(fn, "__athena_scenario__", None)
        if sc is not None:
            return sc, fn
    return None, None


async def _run(args: argparse.Namespace) -> int:
    sc, fn = _find_scenario(args.name)
    if sc is None:
        print(f"Scenario {args.name!r} not found", file=sys.stderr)
        return 2
    runner = EvalRunner(judge=get_judge(choice=args.judge))
    set_runner(runner)
    er = await runner.run(sc, {"question": args.question or "", "scenario_name": sc.name})
    print(f"Scenario:  {sc.name}")
    print(f"Dataset:   {sc.dataset}")
    print(f"Question:  {er.question}")
    print(f"Answer:    {er.answer[:300]}{'...' if len(er.answer) > 300 else ''}")
    print(f"Passed:    {er.passed}")
    for n, s in er.scores.items():
        print(f"  {n}: {s.score:.2f}  {s.explanation}")
    if er.error:
        print(f"Error:     {er.error}")
    write_all(runner.stream_path)
    return 0 if er.passed else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("name", help="Scenario function name (e.g. test_capital_of_france)")
    p.add_argument("--question", help="Override the question (for ad-hoc probing)")
    p.add_argument("--judge", choices=["ollama", "openai", "heuristic"],
                   default="ollama")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
