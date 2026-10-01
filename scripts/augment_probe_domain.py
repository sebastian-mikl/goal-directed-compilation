#!/usr/bin/env python
"""Augment probe_domain/employees.pkl with 4 new numeric columns for T5 depth.

Context: the employee domain's original 3 numeric columns (Annual Salary, Bonus
Target, Tenure (Years)) are enough for T1-T4 but too thin for a real T5 tier
(risk's T5 chains 2+ derived columns across 7 numeric source columns). This adds
4 columns calibrated to the EXISTING data's actual generative structure, verified
directly before writing this script: Annual Salary is almost purely Role-banded
(clean, non-overlapping ranges per role), Department has ~zero effect on any
numeric column, Bonus Target is an exact deterministic function of Role, and
Tenure is independent noise (uncorrelated with Role/Salary/Bonus). The new
columns follow that same pattern rather than inventing a different one:

  Performance Score      float, 1dp, Normal(3.2, 0.6) clipped [1.0, 5.0], weak
                          Tenure-scaled correlation (target r ~ 0.2) -- NOT tied
                          to Role/Salary (real performance ratings are weak
                          predictors of comp band; nothing else in this dataset
                          ties to Tenure strongly either).
  Market Rate             int, Role-banded (mean/std per role below), negligible
                          Department effect -- mirrors Salary's own structure.
                          Offset from Salary's realized bands so Salary-minus-
                          Market-Rate isn't trivially ~0.
  Equity Grant Value      int, Role-band base x (1 + 0.03*Tenure) + ~5% noise --
                          the one place a real Tenure correlation is genuinely
                          motivated (equity refresh grants are a real HR
                          mechanism), unlike Performance Score's weaker one.
  Time in Current Role    float, 1dp, = Tenure * Uniform(0.2, 1.0), hard
  (Years)                 constraint <= Tenure always (structural, not
                          statistical -- can't be in your current role longer
                          than you've worked here).

Seed is fixed and drawn from ONE rng instance across all 4 columns, in the
order above, so re-running this script reproduces byte-identical output.

    python scripts/augment_probe_domain.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PKL_PATH = ROOT / "evaluation/domains/employee" / "employees.pkl"

RNG_SEED = 42  # arbitrary, fixed for reproducibility -- rerunning this script
               # with this seed reproduces this exact augmentation byte-for-byte

ORIGINAL_COLUMNS = ["Employee ID", "Name", "Department", "Role", "Status",
                     "Location", "Annual Salary", "Bonus Target",
                     "Tenure (Years)", "Manager ID"]

MARKET_RATE_BY_ROLE = {"IC": (98_000, 9_000), "Manager": (150_000, 10_000),
                        "Director": (215_000, 12_000), "VP": (300_000, 14_000)}
EQUITY_BASE_BY_ROLE = {"IC": 15_000, "Manager": 35_000, "Director": 70_000, "VP": 150_000}


def augment(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    df = df.copy()
    tenure = df["Tenure (Years)"].to_numpy(dtype=float)
    n = len(df)

    # ── Performance Score: Normal(3.2, 0.6) clipped [1,5], r~0.2 with Tenure ──
    z_tenure = (tenure - tenure.mean()) / tenure.std()
    z_noise = rng.normal(0, 1, size=n)
    w = 0.2                                    # target correlation coefficient
    combined = w * z_tenure + np.sqrt(1 - w**2) * z_noise    # unit variance, corr(., z_tenure) = w
    perf = np.clip(3.2 + 0.6 * combined, 1.0, 5.0)
    df["Performance Score"] = np.round(perf, 1)

    # ── Market Rate: Role-banded, negligible Department effect ───────────────
    market_rate = np.array([rng.normal(*MARKET_RATE_BY_ROLE[r]) for r in df["Role"]])
    df["Market Rate"] = np.round(market_rate).astype(int)

    # ── Equity Grant Value: Role-band base x (1+0.03*Tenure) + ~5% noise ──────
    base = np.array([EQUITY_BASE_BY_ROLE[r] for r in df["Role"]], dtype=float)
    equity = base * (1 + 0.03 * tenure) + rng.normal(0, 1, size=n) * (0.05 * base)
    df["Equity Grant Value"] = np.round(equity).astype(int)

    # ── Time in Current Role: Tenure * U(0.2, 1.0), hard-clipped <= Tenure ────
    frac = rng.uniform(0.2, 1.0, size=n)
    raw = np.maximum(tenure * frac, 0.1)
    time_in_role = np.minimum(raw, tenure)                 # enforce constraint pre-rounding
    time_in_role = np.round(time_in_role, 1)
    time_in_role = np.minimum(time_in_role, tenure)         # re-enforce post-rounding
    df["Time in Current Role (Years)"] = time_in_role

    return df


def main() -> int:
    if not PKL_PATH.exists():
        print(f"ERROR: {PKL_PATH} not found.")
        return 2

    original = pd.read_pickle(PKL_PATH)
    assert list(original.columns) == ORIGINAL_COLUMNS, \
        f"unexpected existing column set: {list(original.columns)}"
    assert len(original) == 150, f"unexpected row count: {len(original)}"

    rng = np.random.default_rng(RNG_SEED)
    augmented = augment(original, rng)

    # verify the original 10 columns are byte-identical before writing anything
    for col in ORIGINAL_COLUMNS:
        assert augmented[col].equals(original[col]), f"column {col!r} was modified!"
    assert len(augmented) == 150
    assert list(augmented.columns) == ORIGINAL_COLUMNS + [
        "Performance Score", "Market Rate", "Equity Grant Value", "Time in Current Role (Years)"]

    augmented.to_pickle(PKL_PATH)
    print(f"Wrote {PKL_PATH} -- {len(augmented)} rows x {len(augmented.columns)} columns "
          f"({len(ORIGINAL_COLUMNS)} original + 4 new), seed={RNG_SEED}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
