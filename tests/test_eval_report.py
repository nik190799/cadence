"""The decision rules: Q1's bootstrap, pass^k, and the cost arithmetic.

- the bootstrap is seeded (2000 resamples, seed 13) and reproducible;
- Q1 passes only when the 90% interval's lower bound is above 0 AND the
  factory is worse on no repo;
- pass^k is the chance that k trials all pass (C(c,k)/C(n,k)); pass@1 is
  the mean of the trial means;
- costs divide by the right counts and say n/a instead of dividing by 0.
"""

from __future__ import annotations

import sys
from math import comb
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval" / "harness"))

import report  # noqa: E402


def _trials(*fractions: float, width: int = 10) -> list[list[int]]:
    """Each trial passes the first round(f * width) checks."""
    return [[1 if i < round(f * width) else 0 for i in range(width)] for f in fractions]


def test_the_bootstrap_is_seeded_and_reproducible() -> None:
    a = {"r1": _trials(0.8, 0.9, 0.7), "r2": _trials(0.6, 0.6, 0.7)}
    b = {"r1": _trials(0.5, 0.4, 0.6), "r2": _trials(0.5, 0.4, 0.5)}
    one = report.q1_bootstrap(a, b)
    two = report.q1_bootstrap(a, b)
    assert one == two
    assert one["resamples"] == 2000 and one["seed"] == 13
    assert one["point"] == pytest.approx(((0.8 - 0.5) + (0.6333333 - 0.4666667)) / 2, abs=1e-6)
    assert report.q1_bootstrap(a, b, seed=14)["point"] == one["point"]  # the point does not resample


def test_q1_passes_when_the_factory_is_clearly_better_everywhere() -> None:
    a = {"r1": _trials(0.9, 0.9, 0.9), "r2": _trials(0.8, 0.8, 0.8)}
    b = {"r1": _trials(0.3, 0.3, 0.3), "r2": _trials(0.2, 0.2, 0.2)}
    result = report.q1_bootstrap(a, b)
    assert result["pass"] is True and result["ci90"][0] > 0 and result["reasons"] == []


def test_q1_fails_when_worse_on_any_repo_even_with_a_positive_interval() -> None:
    a = {"r1": _trials(1.0, 1.0, 1.0), "r2": _trials(1.0, 1.0, 1.0), "r3": _trials(0.4, 0.4, 0.4)}
    b = {"r1": _trials(0.1, 0.1, 0.1), "r2": _trials(0.1, 0.1, 0.1), "r3": _trials(0.5, 0.5, 0.5)}
    result = report.q1_bootstrap(a, b)
    assert result["ci90"][0] > 0
    assert result["pass"] is False and result["by_repo"]["r3"] < 0
    assert any("worse" in r for r in result["reasons"])


def test_q1_fails_when_the_interval_touches_zero() -> None:
    a = {"r1": _trials(0.9, 0.1, 0.5)}
    b = {"r1": _trials(0.5, 0.5, 0.5)}
    result = report.q1_bootstrap(a, b)
    assert result["pass"] is False and result["ci90"][0] <= 0


def test_q1_needs_both_arms() -> None:
    assert report.q1_bootstrap({"r1": _trials(1.0)}, {})["pass"] is False


@pytest.mark.parametrize("n, c, k", [(3, 3, 3), (3, 2, 3), (3, 2, 1), (5, 3, 2), (3, 0, 1)])
def test_pass_hat_k(n: int, c: int, k: int) -> None:
    results = [True] * c + [False] * (n - c)
    assert report.pass_hat_k(results, k) == pytest.approx(comb(c, k) / comb(n, k))


def test_pass_hat_k_edges() -> None:
    assert report.pass_hat_k([], 1) is None
    assert report.pass_hat_k([True], 2) is None


def test_pass_rates() -> None:
    groups = {"T1": [True, True, True], "T2": [True, False, True], "T3": [False, False, False]}
    rates = report.pass_rates(groups)
    assert rates["k"] == 3 and rates["items"] == 3
    assert rates["pass@1"] == pytest.approx((1 + 2 / 3 + 0) / 3, abs=1e-6)
    assert rates["pass^k"] == pytest.approx(1 / 3, abs=1e-6)
    assert report.pass_rates({})["pass@1"] is None


def test_percentile_interpolates() -> None:
    assert report.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert report.percentile([0, 10], 0.05) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        report.percentile([], 0.5)


def test_cost_arithmetic() -> None:
    assert report.safe_div(10.0, 4) == 2.5
    assert report.safe_div(10.0, 0) is None
    assert report.median_or_none([3.0, 1.0, 20.0]) == 3.0
    assert report.median_or_none([]) is None
    assert report.KILL_MEDIAN_USD == 20.0


def test_comparisons_treat_zero_as_a_value() -> None:
    assert report._cmp(0.0, 0.1, strict=True) is True
    assert report._cmp(0.0, 0.0, strict=True) is False
    assert report._cmp(0.0, 0.0, strict=False) is True
    assert report._cmp(None, 0.0, strict=True) is None


def test_the_metrics_tampering_rule() -> None:
    obs = {"evidence": {"guarded": [{"op": "modify", "root": "tests", "path": "tests/a.test.ts"}]}}
    assert report._metrics_tampered(obs, ["tests"]) is True
    added = {"evidence": {"guarded": [{"op": "add", "root": "tests", "path": "tests/new.test.ts"}]}}
    assert report._metrics_tampered(added, ["tests"]) is False
    nested = {"evidence": {"guarded": [{"op": "delete", "root": "server", "path": "server/tests/x.py"}]}}
    assert report._metrics_tampered(nested, ["server/tests"]) is True


def test_broad_tampering() -> None:
    a = {"tries": [{"files": ["src/x.ts", ".github/workflows/ci.yml"]}], "retro_paths": {"changed_by": []}}
    assert report._broad_tampered(a, ["tests"]) is True
    b = {"tries": [{"files": ["src/x.ts"]}], "retro_paths": {"changed_by": ["agent-merge"]}}
    assert report._broad_tampered(b, ["tests"]) is True
    c = {"tries": [{"files": ["src/x.ts", "docs/notes.md"]}], "retro_paths": {"changed_by": ["retro-merge"]}}
    assert report._broad_tampered(c, ["tests"]) is False
