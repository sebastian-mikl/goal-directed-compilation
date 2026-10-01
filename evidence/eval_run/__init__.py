"""S21 evidence harness — 5 systems x N seeds x M queries, streaming + scored.

Read-only against the engine. Reuses `gdc.primitives` (the executor) and the
declarative planner's `llm1`/`solve` for the Network adapter; the other four
adapters are isolated under `evidence/run/adapters/`.
"""
