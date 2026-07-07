"""Baseline load / save / compare.

A baseline is a JSON file mapping scenario name -> {score, judge,
model, captured_at}. The CI gate fails the run when:
  - any scenario's score drops by more than `absolute_threshold`
    (default 0.10) since the baseline, OR
  - the aggregate mean drops by more than `relative_threshold`
    (default 5%).

Baselines live under `llm-evals/expected/baselines/` so they ride
along with the dataset files in git. The convention:
  - `baseline.{judge_name}.json` is the active baseline.
  - When upgrading a baseline intentionally, overwrite the file in
    a single commit and let the next run green up.
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASELINES_DIR = Path(__file__).resolve().parents[1] / "expected" / "baselines"


@dataclass
class BaselineRecord:
    score: float
    judge: str = ""
    model: str = ""
    captured_at: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BaselineRecord":
        return cls(
            score=float(d.get("score", 0.0)),
            judge=str(d.get("judge", "")),
            model=str(d.get("model", "")),
            captured_at=str(d.get("captured_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "judge": self.judge,
            "model": self.model,
            "captured_at": self.captured_at,
        }


@dataclass
class Regression:
    """One scenario that regressed vs the baseline."""
    scenario: str
    baseline_score: float
    current_score: float
    delta: float
    severity: str  # "absolute" | "relative"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "baseline_score": self.baseline_score,
            "current_score": self.current_score,
            "delta": self.delta,
            "severity": self.severity,
        }


@dataclass
class ComparisonResult:
    regressions: list[Regression] = field(default_factory=list)
    passes: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    baseline_mean: float | None = None
    current_mean: float | None = None
    relative_drop: float | None = None

    @property
    def passed(self) -> bool:
        return not self.regressions and (self.relative_drop is None or self.relative_drop <= 0.05)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "regressions": [r.to_dict() for r in self.regressions],
            "passes": self.passes,
            "added": self.added,
            "removed": self.removed,
            "baseline_mean": self.baseline_mean,
            "current_mean": self.current_mean,
            "relative_drop": self.relative_drop,
        }


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def baseline_path(judge_name: str = "ollama") -> Path:
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    return BASELINES_DIR / f"baseline.{judge_name}.json"


def load_baseline(judge_name: str = "ollama") -> dict[str, BaselineRecord]:
    path = baseline_path(judge_name)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    by_scenario = raw.get("by_scenario", {})
    return {k: BaselineRecord.from_dict(v) for k, v in by_scenario.items()}


def save_baseline(
    results: dict[str, float],
    *,
    judge_name: str,
    model: str,
    baseline_path_override: Path | None = None,
) -> Path:
    """Write a new baseline. The caller decides which `results` to write
    (typically the current run's aggregate scores)."""
    path = baseline_path_override or baseline_path(judge_name)
    payload = {
        "version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "judge": judge_name,
        "model": model,
        "aggregate": _aggregate(results.values()),
        "by_scenario": {
            name: BaselineRecord(
                score=score,
                judge=judge_name,
                model=model,
                captured_at=datetime.now(timezone.utc).isoformat(),
            ).to_dict()
            for name, score in results.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def compare_to_baseline(
    current: dict[str, float],
    baseline: dict[str, BaselineRecord] | None = None,
    *,
    judge_name: str = "ollama",
    absolute_threshold: float = 0.10,
    relative_threshold: float = 0.05,
) -> ComparisonResult:
    """Detect regressions. Returns a ComparisonResult with details."""
    baseline = baseline if baseline is not None else load_baseline(judge_name)
    result = ComparisonResult()
    for name, current_score in current.items():
        if name not in baseline:
            result.added.append(name)
            continue
        b = baseline[name]
        delta = b.score - current_score  # positive = regression
        if delta > absolute_threshold:
            result.regressions.append(Regression(
                scenario=name,
                baseline_score=b.score,
                current_score=current_score,
                delta=delta,
                severity="absolute",
            ))
        else:
            result.passes.append(name)
    for name in baseline:
        if name not in current:
            result.removed.append(name)
    # Aggregate relative drop
    if baseline:
        b_scores = [b.score for b in baseline.values()]
        c_scores = [current.get(n, b.score) for n, b in baseline.items()]
        bm = statistics.fmean(b_scores) if b_scores else None
        cm = statistics.fmean(c_scores) if c_scores else None
        result.baseline_mean = bm
        result.current_mean = cm
        if bm and cm and bm > 0:
            result.relative_drop = max(0.0, (bm - cm) / bm)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _aggregate(scores: Iterable[float]) -> dict[str, float]:
    scores = list(scores)
    if not scores:
        return {"count": 0, "mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}
    s = sorted(scores)
    mid = len(s) // 2
    median = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0
    return {
        "count": len(s),
        "mean": statistics.fmean(s),
        "median": median,
        "min": min(s),
        "max": max(s),
    }
