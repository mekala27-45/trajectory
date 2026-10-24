"""The demo reads both committed run sets and tells the reader which rows are which."""

from __future__ import annotations

import json
from pathlib import Path

import build_web_bundle as bundle

ROOT = Path(__file__).resolve().parent.parent


def _index(out: Path) -> dict:
    return json.loads((out / "index.json").read_text(encoding="utf-8"))


def test_from_fixtures_reads_the_scripted_and_the_real_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    out = tmp_path / "data"
    assert bundle.main(["--from-fixtures", "--out", str(out)]) == 0

    index = _index(out)
    committed = sum(len(list(d.glob("*.json"))) for d in bundle.DEFAULT_FIXTURES)
    assert index["run_count"] == committed
    assert any(m.startswith("stub:") for m in index["models"])
    assert any(not m.startswith("stub:") for m in index["models"])
    # Both halves of the note, because a mixed table without them misleads.
    assert bundle.STUB_NOTE in index["note"]
    assert bundle.REAL_NOTE in index["note"]


def test_a_named_directory_reads_only_that_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    out = tmp_path / "data"
    assert bundle.main(["--from-fixtures", "fixtures/recorded-runs", "--out", str(out)]) == 0

    index = _index(out)
    assert all(m.startswith("stub:") for m in index["models"])
    assert bundle.STUB_NOTE in index["note"]
    assert bundle.REAL_NOTE not in index["note"]
