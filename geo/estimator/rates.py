"""V0 estimator: rates with Wilson intervals, and a minimum-detectable-effect helper.

V2 replaces the per-intent rate with a hierarchical logistic model (random effects for
prompt variant, day, engine) and reports a variance decomposition.
"""
from __future__ import annotations

import math


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


def runs_for_mde(p0: float, mde: float = 0.10, alpha: float = 0.05, power: float = 0.8) -> int:
    """Runs per arm to detect a change of `mde` from baseline rate p0 (two-proportion test).
    Ignores clustering by variant/day, so it is a LOWER bound until variance decomposition exists."""
    from statistics import NormalDist

    z_a = NormalDist().inv_cdf(1 - alpha / 2)
    z_b = NormalDist().inv_cdf(power)
    p1 = min(0.999, p0 + mde)
    pbar = (p0 + p1) / 2
    num = (z_a * math.sqrt(2 * pbar * (1 - pbar)) + z_b * math.sqrt(p0 * (1 - p0) + p1 * (1 - p1))) ** 2
    return math.ceil(num / (mde ** 2))
