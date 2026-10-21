#!/usr/bin/env python3
"""Export the blueprint's runtime contract blocks to JSON that production code reads.

Runtime code never parses markdown: it reads `src/hearthward/contracts/spec/envelope.schema.json`,
`temporal.json`, `tools.json` and `hx.json`, which this tool writes from the `hearthward:envelope`,
`hearthward:temporal`, `hearthward:tools` and `hearthward:hx` blocks.
The output is byte-deterministic (sorted keys, two-space indent, LF, trailing newline), so a test can
re-export and compare bytes to catch drift between the blueprint and the committed files.

    python tools/export_spec.py
    python tools/export_spec.py --out some/dir --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orrery  # same directory: owns the blueprint parser

OUT_DIR = orrery.ROOT / "src" / "hearthward" / "contracts" / "spec"
EXPORTS = {"envelope": "envelope.schema.json", "temporal": "temporal.json", "tools": "tools.json", "hx": "hx.json"}


def render(value: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, two-space indent, LF line endings, trailing newline."""
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def export(spec: dict[str, Any], out_dir: Path = OUT_DIR) -> dict[Path, bytes]:
    """Write every exported block to `out_dir` in binary mode; returns what was written, by path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for block, name in EXPORTS.items():
        path = out_dir / name
        data = render(spec[block])
        path.write_bytes(data)
        written[path] = data
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the runtime blueprint blocks (envelope, temporal, tools, hx) to JSON.")
    parser.add_argument("--spec", type=Path, default=orrery.BLUEPRINT, help="blueprint to read the blocks from")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="directory to write the JSON files to")
    parser.add_argument("--check", action="store_true", help="compare with the files on disk instead of writing")
    args = parser.parse_args(argv)
    try:
        spec = orrery.load_spec(args.spec)
    except orrery.SpecError as exc:
        print(f"export_spec: {exc}", file=sys.stderr)
        return 2
    if args.check:
        stale = [name for block, name in EXPORTS.items()
                 if not (args.out / name).exists() or (args.out / name).read_bytes() != render(spec[block])]
        for name in stale:
            print(f"stale: {args.out / name}")
        return 1 if stale else 0
    for path in export(spec, args.out):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
