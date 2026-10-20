"""Spec drift: the committed runtime JSON equals a fresh export of the blueprint blocks, byte for byte."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import export_spec
import pytest

from hearthward.contracts import specs
from tests.support import read_json

pytestmark = pytest.mark.gate_g1


def test_reexport_is_byte_identical_to_the_committed_files(spec: dict[str, Any], tmp_path: Path) -> None:
    written = export_spec.export(spec, tmp_path)
    assert sorted(p.name for p in written) == ["envelope.schema.json", "temporal.json"]
    for path, data in written.items():
        assert (specs.SPEC_DIR / path.name).read_bytes() == data == path.read_bytes()


def test_export_is_deterministic(spec: dict[str, Any], tmp_path: Path) -> None:
    first = export_spec.export(spec, tmp_path / "a")
    second = export_spec.export(spec, tmp_path / "b")
    assert [d for d in first.values()] == [d for d in second.values()]


def test_exported_bytes_are_lf_terminated_ascii(spec: dict[str, Any]) -> None:
    for name in export_spec.EXPORTS.values():
        data = (specs.SPEC_DIR / name).read_bytes()
        assert b"\r" not in data and data.endswith(b"}\n") and data.isascii()


def test_runtime_loaders_return_the_blueprint_blocks(spec: dict[str, Any]) -> None:
    assert specs.envelope_schema() == spec["envelope"]
    assert specs.temporal() == spec["temporal"]
    assert read_json(specs.SPEC_DIR / "temporal.json") == spec["temporal"]


def test_check_mode_reports_drift(spec: dict[str, Any], tmp_path: Path) -> None:
    export_spec.export(spec, tmp_path)
    assert export_spec.main(["--out", str(tmp_path), "--check"]) == 0
    (tmp_path / "temporal.json").write_bytes(b"{}\n")
    assert export_spec.main(["--out", str(tmp_path), "--check"]) == 1
