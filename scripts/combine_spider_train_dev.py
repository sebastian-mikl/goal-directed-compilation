#!/usr/bin/env python
"""Combine Spider's train + validation(dev) Q&A data into one file, so
rank_all_spider_databases.py sees a database's FULL real-world question
coverage, not just the dev/validation-split subset.

Context: the dev-only ranking found only 20 of 166 local databases have any
question coverage at all (dev_data.parquet's 1034 rows span exactly 20
db_ids; the other 146 databases show quest=0) — a coverage artifact of
dev_data.parquet being the VALIDATION split only, not a schema-quality
problem. Spider's train split (xlangai/spider's 'train' split, ~7000
questions) has real, independently-authored gold SQL of the same kind and
legitimacy as dev for OUR purposes — we're mining Spider for third-party
evaluation content, not submitting to a leaderboard, so the train/dev
separation that matters for ML generalization testing doesn't constrain us
here. Spider's train and dev/test databases are disjoint BY DESIGN (a
foundational property of the benchmark — held-out databases test cross-
database generalization), so combining is expected to surface NEW db_id
coverage, not duplicate rows for db_ids dev already covers; this script
checks that expectation empirically rather than assuming it (see the
overlap report below) and is safe either way (a plain union is the right
combined-coverage answer regardless of overlap).

This does NOT modify or replace rank_all_spider_databases.py — that script
is unchanged and already verified; combine first, then point it at the
combined file:

    python scripts/combine_spider_train_dev.py train_data.parquet dev_data.parquet
    python scripts/rank_all_spider_databases.py /path/to/spider/database combined_data.parquet

Read-only over its two inputs; writes exactly one new file (--output,
default combined_data.parquet).

train_data.parquet is not included in this repo. Pull it first, then run:

    python3 -c "
    from datasets import load_dataset
    ds = load_dataset('xlangai/spider')
    ds['train'].to_pandas().to_parquet('train_data.parquet')
    "
    python scripts/combine_spider_train_dev.py train_data.parquet dev_data.parquet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = {"db_id", "query", "question"}


def load(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return pd.DataFrame(json.loads(path.read_text()))
    if suffix in (".parquet", ".pq"):
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"unrecognized file type {path.suffix!r} (expected .json/.parquet/.csv)")


def combine(train_df: pd.DataFrame, dev_df: pd.DataFrame) -> pd.DataFrame:
    missing_train = REQUIRED_COLUMNS - set(train_df.columns)
    missing_dev = REQUIRED_COLUMNS - set(dev_df.columns)
    if missing_train or missing_dev:
        raise ValueError(
            f"column mismatch -- train missing {missing_train or 'none'}, "
            f"dev missing {missing_dev or 'none'} (both need at least {REQUIRED_COLUMNS})")

    train_dbs = set(train_df["db_id"].unique())
    dev_dbs = set(dev_df["db_id"].unique())
    overlap = train_dbs & dev_dbs
    print(f"train: {len(train_df)} questions across {len(train_dbs)} db_ids")
    print(f"dev:   {len(dev_df)} questions across {len(dev_dbs)} db_ids")
    if overlap:
        print(f"NOTE: {len(overlap)} db_id(s) appear in BOTH train and dev -- unexpected, Spider's "
              f"train/dev databases are normally disjoint by design; double check your inputs are "
              f"really the train and validation splits of the same dataset. Combining anyway (union "
              f"of both sides' coverage is correct either way): {sorted(overlap)[:10]}")
    else:
        print("db_id overlap between train and dev: none (expected, per Spider's disjoint-database design)")

    combined = pd.concat([train_df, dev_df], ignore_index=True)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["db_id", "question", "query"], keep="first")
    after = len(combined)
    if before != after:
        print(f"dropped {before - after} exact-duplicate (db_id, question, query) row(s)")

    return combined


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("train_data", type=Path, help="Spider train split: .json/.parquet/.csv")
    p.add_argument("dev_data", type=Path, help="Spider dev/validation split: .json/.parquet/.csv")
    p.add_argument("--output", type=Path, default=Path("combined_data.parquet"))
    args = p.parse_args()

    if not args.train_data.exists():
        print(f"ERROR: {args.train_data} does not exist.")
        return 2
    if not args.dev_data.exists():
        print(f"ERROR: {args.dev_data} does not exist.")
        return 2

    try:
        train_df = load(args.train_data)
        dev_df = load(args.dev_data)
        combined = combine(train_df, dev_df)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1
    combined.to_parquet(args.output)
    print(f"\nwrote {len(combined)} combined questions across "
          f"{combined['db_id'].nunique()} db_ids -> {args.output}")
    print(f"\nnext: python scripts/rank_all_spider_databases.py /path/to/spider/database {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
