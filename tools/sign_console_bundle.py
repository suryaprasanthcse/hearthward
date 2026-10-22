#!/usr/bin/env python3
"""HUMAN-RUN: sign the console's decision record with the deployed AWS KMS attest-signer key.

    uv run python tools/sign_console_bundle.py                  # needs your AWS credentials; writes the bundle
    uv run python tools/sign_console_bundle.py --dry-run        # rebuild and check the record offline; no AWS call

It rebuilds the same canonical decision record `tools/export_ui_scenario.py` signs with its synthetic key, then
asks KMS (`alias/hearthward/attest-signer`, ECDSA_SHA_256, MessageType RAW) to sign those exact bytes, fetches the
public key with GetPublicKey, verifies the result offline with `tools/verify_attestation.py`, and only then writes
`web/console/data/bundle.kms.json`. Claude never runs this (CLAUDE.md rule 3); you do, with your own credentials.

The signature is a REAL KMS signature over a SYNTHETIC record, and the bundle says so (blueprint T-5).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_ui_scenario as scenario
import orrery
import verify_attestation as va

ALIAS = "alias/hearthward/attest-signer"
OUT = orrery.ROOT / "web" / "console" / "data" / "bundle.kms.json"
TOOL = "tools/sign_console_bundle.py"


def load_dotenv(path: Path) -> None:
    """Fill os.environ from the git-ignored .env without overriding what the shell already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=orrery.ROOT, capture_output=True, text=True, check=True).stdout.strip()


def rebuild() -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    """The canonical decision bytes, the decision record parsed from them, and the ledger envelopes."""
    built = scenario.build()
    data = base64.b64decode(built["attestation"]["decision"]["bytes_b64"], validate=True)
    assert data == va.jcs(va.parse_strict(data)), "the record is not in canonical form"
    return data, va.parse_strict(data), built["envelopes"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sign the console decision record with AWS KMS (human-run).")
    parser.add_argument("--alias", default=ALIAS, help=f"KMS key alias or ARN (default {ALIAS})")
    parser.add_argument("--region", help="AWS region (default: AWS_REGION from the environment or .env)")
    parser.add_argument("--out", type=Path, default=OUT, help=f"output file (default {OUT.relative_to(orrery.ROOT)})")
    parser.add_argument("--dry-run", action="store_true", help="rebuild and check the record; make no AWS call")
    args = parser.parse_args(argv)
    load_dotenv(orrery.ROOT / ".env")

    data, record, ledger = rebuild()
    print(f"record: {record['episode']} {record['decision']} lambda {record['lambda_mnats']} mnats at seq "
          f"{record['at_seq']} ({len(data)} canonical bytes, {len(ledger)} envelopes)")
    if args.dry_run:
        print("dry run: nothing sent to AWS, nothing written")
        return 0

    import boto3
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_der_public_key

    region = args.region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    kms = boto3.client("kms", region_name=region)
    meta = kms.describe_key(KeyId=args.alias)["KeyMetadata"]
    if meta.get("KeySpec") != "ECC_NIST_P256" or meta.get("KeyUsage") != "SIGN_VERIFY":
        print(f"refusing: {meta['Arn']} is {meta.get('KeySpec')}/{meta.get('KeyUsage')}, not ECC_NIST_P256/SIGN_VERIFY",
              file=sys.stderr)
        return 1
    arn = meta["Arn"]
    signature = kms.sign(KeyId=arn, Message=data, MessageType="RAW", SigningAlgorithm="ECDSA_SHA_256")["Signature"]
    public = load_der_public_key(kms.get_public_key(KeyId=arn)["PublicKey"])
    spki = public.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)

    bundle: dict[str, Any] = {
        "v": 1,
        "public_key_b64": base64.b64encode(spki).decode("ascii"),
        "decision": {"bytes_b64": base64.b64encode(data).decode("ascii"),
                     "sig_b64": base64.b64encode(signature).decode("ascii"), "key_id": arn},
        "decision_digest": va.tagged(b"hw-decision-v1", data),
        "sprt_params": orrery.load_spec()["sprt"],
        "ledger": ledger,
        "kms": {"key_arn": arn, "key_alias": args.alias, "region": kms.meta.region_name, "key_spec": meta["KeySpec"],
                "signing_algorithm": "ECDSA_SHA_256", "message_type": "RAW",
                "signed_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "git_commit": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
                "tool": TOOL, "record_is_synthetic": True,
                "note": "A real AWS KMS signature over a SYNTHETIC decision record (orrery doorstep_scam, seed 7)."},
    }
    checks = va.verify_bundle(bundle)
    for check in checks:
        print(f"{'PASS' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    if not all(c.ok for c in checks):
        print("refusing to write a bundle that does not verify", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes((json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
