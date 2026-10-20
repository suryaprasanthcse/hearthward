"""The orrery's fixture export is byte-deterministic and matches the committed fixtures (gate G1.fixtures)."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

import orrery
import pytest

from tests.support import FIXTURES, read_json

pytestmark = pytest.mark.gate_g1

FILES = ("ring/sample.json", "ring/manifest.json", "bee/stream.jsonl", "bee/manifest.json")

# --selftest trace digests before the fixture export was added: the export must not change the simulation
BASELINE_TRACES = {
    "doorstep_scam": "sha256:9e2f22791794db7d260f553a979633cb2a9f1fa9013890f49482e8124b6cda44",
    "late_and_lossy": "sha256:8aca84bbf60a34da878afce84bf9023eaf896eeb71b5ea5bd1dd5bc12e71b393",
    "skewed_door": "sha256:1d427b5c7d19b18d7a01f25ad9573698936fb001c77c3e46341aff400a311bf5",
    "grandparent_call": "sha256:a1502156a04dd33b2ec1b688c68b80987de67aff47ab18d32ca8b3bf76f685b8",
    "benign_delivery": "sha256:fad82553c43880e39460da45c990459485d2e4c4b5f76180b4b13bb46f6db678",
    "verified_plumber": "sha256:6a35e864a8aaf6cb1ff0f5c7903681039ab997a5357cf8c750753756b6c98cca",
}


def test_reexport_is_byte_identical_to_the_committed_fixtures(spec: dict[str, Any], tmp_path: Path) -> None:
    written = orrery.export_fixtures(spec, tmp_path)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in written) == sorted(FILES)
    for name in FILES:
        assert (tmp_path / name).read_bytes() == (FIXTURES / name).read_bytes(), name


def test_two_exports_are_identical(spec: dict[str, Any], tmp_path: Path) -> None:
    orrery.export_fixtures(spec, tmp_path / "a")
    orrery.export_fixtures(spec, tmp_path / "b")
    for name in FILES:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name


def test_selftest_traces_are_unchanged(orrery_runs: dict[str, orrery.Result]) -> None:
    assert {name: r.summary["trace_digest"] for name, r in orrery_runs.items()} == BASELINE_TRACES


def test_fixtures_are_lf_only_and_signed_with_the_synthetic_keys() -> None:
    for name in FILES:
        assert b"\r" not in (FIXTURES / name).read_bytes(), name
    ring = read_json(FIXTURES / "ring" / "manifest.json")
    raw = (FIXTURES / "ring" / "sample.json").read_bytes()
    assert ring["key_label"] == "synthetic"
    assert ring["x_signature"] == "sha256=" + hmac.new(orrery.SYNTHETIC_RING_KEY, raw, hashlib.sha256).hexdigest()
    bee = read_json(FIXTURES / "bee" / "manifest.json")
    lines = (FIXTURES / "bee" / "stream.jsonl").read_bytes().split(b"\n")
    assert lines[-1] == b"" and len(lines) - 1 == len(bee["lines"])
    for line, entry in zip(lines, bee["lines"]):
        assert entry["signature"] == hmac.new(orrery.SYNTHETIC_BRIDGE_KEY, line, hashlib.sha256).hexdigest()
