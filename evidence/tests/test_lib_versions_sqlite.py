"""Every harness provenance records the SQLite text_to_sql runs on.

A SQLite built with the percentile functions answers MEDIAN; one without fails
with 'no such function', which text_to_sql records as a refusal. So the SQLite
build alone can move a text_to_sql cell between a decline and an answer, and
lib_versions records it. No API call.
"""
from __future__ import annotations

import sqlite3

from evidence.eval_run.logger import _lib_versions, sqlite_info


def test_lib_versions_record_the_sqlite():
    v = _lib_versions()
    assert v["sqlite"] == sqlite3.sqlite_version and "sqlite3_module" in v
    info = sqlite_info()
    assert info["sqlite_version"] == sqlite3.sqlite_version
    assert set(info["functions"]) == {"median", "percentile", "corr"}
    assert info["functions"]["corr"] is False                # no SQLite build has CORR
