"""Gate G6: decision signing, Merkle anchors, the stdlib-only offline verifier and the web pages' tooling."""

from __future__ import annotations

import ast
import base64
import copy
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import orrery
import pipeline
import pytest
import verify_attestation as va
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from hypothesis import given, settings
from hypothesis import strategies as st

from hearthward.attest import anchor as anchor_mod
from hearthward.attest import sign
from hearthward.attest.sign import AttestationError, KmsSigner
from hearthward.authz.broker import action_hash
from hearthward.contracts.canonical import canonical, tagged_digest
from hearthward.contracts.envelope import ZERO_DIGEST, verify_chain

pytestmark = pytest.mark.gate_g6

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
NODE = shutil.which("node")
NPM = shutil.which("npm")
EPISODE = "ep_01HZX3K9M2N4P6Q8R0S2T4V6W8"
BUCKET = "hearthward-compliance-anchors"


def record(**changes: Any) -> dict[str, Any]:
    base = {"episode": EPISODE, "decision": "INTERVENE", "at_seq": 5, "ledger_head": "sha256:" + "ab" * 32,
            "belief_seq": 5, "lambda_mnats": 5123, "reasons": ["SECRECY_REQUEST", "IRREVERSIBLE_PAYMENT"],
            "thresholds_mnats": {"a": 4554, "b": -2986}, "params_digest": "sha256:" + "cd" * 32,
            "extractor_model_ids": ["synthetic-cassette"], "cross_terms": ["door_unknown_overlap"]}
    return {**base, **changes}


@pytest.fixture
def kms(aws: None) -> Any:
    return boto3.client("kms", region_name="us-east-1")


@pytest.fixture
def signer(kms: Any) -> KmsSigner:
    key = kms.create_key(KeySpec="ECC_NIST_P256", KeyUsage="SIGN_VERIFY")["KeyMetadata"]["KeyId"]
    return KmsSigner(kms, key)


def bundle_for(signer: KmsSigner, sprt: dict[str, Any], envelopes: list[dict[str, Any]], at_seq: int,
               with_anchor: bool = True) -> dict[str, Any]:
    envelopes, sprt = copy.deepcopy(envelopes), copy.deepcopy(sprt)  # session-wide fixtures: edit copies only
    rec = record(at_seq=at_seq, belief_seq=at_seq, ledger_head=envelopes[at_seq - 1]["head"],
                 params_digest=sign.params_digest(sprt))
    out: dict[str, Any] = {
        "v": 1, "public_key_b64": base64.b64encode(signer.public_key_der()).decode(),
        "decision": sign.attest_decision(rec, signer),
        "decision_digest": sign.decision_digest(sign.decision_bytes(rec)), "sprt_params": sprt, "ledger": envelopes}
    if with_anchor:
        household = envelopes[0]["household"]
        heads = {household: (len(envelopes), envelopes[-1]["head"]), "hh_other0001": (4, "sha256:" + "11" * 32),
                 "hh_other0002": (9, "sha256:" + "22" * 32)}
        document = anchor_mod.build_anchor(heads, 1_800_000_000_000)
        out["anchor"] = {"attestation": anchor_mod.sign_anchor(document, signer),
                         "inclusion": anchor_mod.inclusion(document, household)}
    return out


# ── decision records ─────────────────────────────────────────────────────────────────────────────────────


def test_a_valid_record_has_canonical_bytes_and_the_hw_decision_digest() -> None:
    rec = record()
    data = sign.decision_bytes(rec)
    assert data == canonical(rec)
    assert sign.decision_digest(data) == tagged_digest(b"hw-decision-v1", rec)
    assert sign.decision_digest(data) == "sha256:" + hashlib.sha256(b"hw-decision-v1\x00" + data).hexdigest()
    assert json.loads(data) == rec


def test_the_digest_is_domain_separated() -> None:
    data = sign.decision_bytes(record())
    assert sign.decision_digest(data) != "sha256:" + hashlib.sha256(data).hexdigest()
    assert sign.decision_digest(data) != sign.digest_bytes(b"hw-anchor-v1", data)


@pytest.mark.parametrize("changes", [
    {"episode": "ep_short"}, {"decision": "intervene"}, {"at_seq": -1}, {"at_seq": True}, {"belief_seq": "5"},
    {"lambda_mnats": 1.5}, {"ledger_head": "sha256:xyz"}, {"params_digest": "md5:abc"},
    {"thresholds_mnats": {}}, {"thresholds_mnats": {"a": 4.554}}, {"reasons": "SECRECY"}, {"reasons": [1]},
    {"extractor_model_ids": None}, {"cross_terms": {"door_unknown_overlap": 0.7}}, {"cross_terms": "x"},
])
def test_invalid_records_are_never_signed(signer: KmsSigner, changes: dict[str, Any]) -> None:
    with pytest.raises(AttestationError):
        sign.attest_decision(record(**changes), signer)


def test_missing_and_extra_fields_are_refused(signer: KmsSigner) -> None:
    short = record()
    short.pop("reasons")
    for bad in (short, {**record(), "extra": 1}, [], None):
        with pytest.raises(AttestationError):
            sign.decision_bytes(bad)  # type: ignore[arg-type]


def test_floats_and_bad_keys_cannot_hide_in_nested_values() -> None:
    with pytest.raises(AttestationError):
        sign.decision_bytes(record(cross_terms={"Bad Key": 1}))
    with pytest.raises(AttestationError):
        sign.decision_bytes(record(thresholds_mnats={"a": 2**53}))


def test_cross_terms_may_be_a_list_or_a_mapping_of_integers() -> None:
    assert sign.decision_bytes(record(cross_terms=[])) and sign.decision_bytes(record(cross_terms={"x": 700}))


def test_params_digest_equals_the_orrerys(spec: dict[str, Any]) -> None:
    text = json.dumps(spec["sprt"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert sign.params_digest(spec["sprt"]) == "sha256:" + hashlib.sha256(text).hexdigest()
    changed = copy.deepcopy(spec["sprt"])
    changed["alpha"] = 0.02
    assert sign.params_digest(changed) != sign.params_digest(spec["sprt"])


def test_a_kms_signature_verifies_with_the_published_key(signer: KmsSigner) -> None:
    attestation = sign.attest_decision(record(), signer)
    assert set(attestation) == {"bytes_b64", "sig_b64", "key_id"} and attestation["key_id"] == signer.key_id
    data, der = base64.b64decode(attestation["bytes_b64"]), base64.b64decode(attestation["sig_b64"])
    assert data == sign.decision_bytes(record())
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    load_der_public_key(signer.public_key_der()).verify(der, data, ec.ECDSA(hashes.SHA256()))


def test_sign_bytes_signs_exactly_the_bytes_it_is_given(signer: KmsSigner) -> None:
    attestation = sign.sign_bytes(b"\x00raw bytes\xff", signer)
    assert base64.b64decode(attestation["bytes_b64"]) == b"\x00raw bytes\xff"


# ── the verifier is standard library only ───────────────────────────────────────────────────────────────


def test_the_verifier_imports_nothing_outside_the_standard_library() -> None:
    tree = ast.parse((ROOT / "tools/verify_attestation.py").read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
    assert not {"hearthward", "cryptography", "rfc8785", "boto3", "orrery"} & imported


def test_the_selftest_exits_zero_in_a_bare_interpreter() -> None:
    """`-I -S` drops site-packages and the working directory: only the standard library is left."""
    proc = subprocess.run([sys.executable, "-I", "-S", str(ROOT / "tools/verify_attestation.py"), "--selftest"],
                          capture_output=True, text=True, timeout=120, cwd=ROOT, check=False)
    assert proc.returncode == 0, proc.stdout[-500:] + proc.stderr[-500:]
    last = proc.stdout.strip().splitlines()[-1]
    assert re.fullmatch(r"selftest: (\d+)/\1 checks passed", last), last
    assert "FAIL" not in proc.stdout


def test_the_pipeline_check_passes() -> None:
    outcome = pipeline.g6_verifier(pipeline.Context(probe=False))
    assert outcome.status == pipeline.PASS and "checks passed" in outcome.detail


def test_the_verifier_agrees_with_cryptography_on_p256() -> None:
    rng = random.Random(6)
    for _ in range(25):
        key = ec.generate_private_key(ec.SECP256R1())
        numbers = key.public_key().public_numbers()
        message = rng.randbytes(rng.randint(0, 200))
        r, s = va.parse_signature(key.sign(message, ec.ECDSA(hashes.SHA256())))
        assert va.ecdsa_verify((numbers.x, numbers.y), message, r, s)
        assert not va.ecdsa_verify((numbers.x, numbers.y), message + b"x", r, s)
        assert not va.ecdsa_verify((numbers.x, numbers.y), message, r, (s % (va.N - 1)) + 1 if s == 1 else s - 1)
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        assert va.parse_public_key(der) == (numbers.x, numbers.y) and va.encode_public_key((numbers.x, numbers.y)) == der


def test_signatures_made_by_the_verifiers_own_signer_verify_in_cryptography() -> None:
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    priv = 0x1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF
    pub = va.mul(priv, va.G)
    r, s = va.ecdsa_sign(priv, b"cross check")
    key = ec.EllipticCurvePublicNumbers(pub[0], pub[1], ec.SECP256R1()).public_key()
    key.verify(encode_dss_signature(r, s), b"cross check", ec.ECDSA(hashes.SHA256()))
    assert va.encode_signature(r, s) == encode_dss_signature(r, s)


@given(st.recursive(st.none() | st.booleans() | st.integers(-2**53 + 1, 2**53 - 1) | st.text(max_size=12),
                    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(
                        st.from_regex(r"[a-z0-9_]{1,6}", fullmatch=True), inner, max_size=3), max_leaves=12))
@settings(max_examples=200, derandomize=True)
def test_the_verifiers_jcs_equals_rfc_8785(value: Any) -> None:
    try:
        expected = canonical(value)
    except Exception:  # noqa: BLE001 - e.g. a lone surrogate, which neither side may serialize
        with pytest.raises(Exception):  # noqa: B017
            va.jcs(value)
        return
    assert va.jcs(value) == expected


def test_the_verifier_replays_the_orreries_ledgers(orrery_runs: dict[str, orrery.Result]) -> None:
    for name, run in orrery_runs.items():
        problems, ledger = va.replay_ledger(run.envelopes)
        assert problems == [] and len(ledger) == len(run.envelopes), name
        assert verify_chain(run.envelopes) == []
        broken = copy.deepcopy(run.envelopes)
        broken[len(broken) // 2]["received_at"] += 1
        assert va.replay_ledger(broken)[0], name


# ── bundles signed by KMS and checked by the stdlib verifier ───────────────────────────────────────────────


@pytest.fixture
def world(orrery_runs: dict[str, orrery.Result], spec: dict[str, Any], signer: KmsSigner) -> dict[str, Any]:
    envelopes = orrery_runs["doorstep_scam"].envelopes
    return bundle_for(signer, spec["sprt"], envelopes, at_seq=len(envelopes) // 2)


def test_a_kms_signed_bundle_passes_every_check(world: dict[str, Any]) -> None:
    checks = va.verify_bundle(world)
    assert [c.name for c in checks] == ["public key", "decision signature", "decision record", "decision digest",
                                        "params digest", "ledger replay", "anchor signature", "anchor root",
                                        "anchor inclusion", "anchor head"]
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]


def test_the_command_line_tool_verifies_and_rejects(world: dict[str, Any], tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps(world), encoding="utf-8")
    ok = subprocess.run([sys.executable, str(ROOT / "tools/verify_attestation.py"), str(good)], capture_output=True,
                        text=True, timeout=60, check=False)
    assert ok.returncode == 0 and ok.stdout.strip().endswith("VERIFIED") and "NOT VERIFIED" not in ok.stdout
    world["ledger"][0]["received_at"] += 1
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(world), encoding="utf-8")
    nope = subprocess.run([sys.executable, str(ROOT / "tools/verify_attestation.py"), str(bad)], capture_output=True,
                          text=True, timeout=60, check=False)
    assert nope.returncode == 1 and "NOT VERIFIED" in nope.stdout and "FAIL  ledger replay" in nope.stdout
    missing = subprocess.run([sys.executable, str(ROOT / "tools/verify_attestation.py"), str(tmp_path / "none.json")],
                             capture_output=True, text=True, timeout=60, check=False)
    assert missing.returncode == 2


@pytest.mark.parametrize("name, mutate, failing", [
    ("record", lambda b: b["decision"].__setitem__("bytes_b64", base64.b64encode(
        base64.b64decode(b["decision"]["bytes_b64"]).replace(b"INTERVENE", b"DISMISSAL")).decode()),
     "decision signature"),
    ("signature", lambda b: b["decision"].__setitem__("sig_b64", base64.b64encode(
        bytes([base64.b64decode(b["decision"]["sig_b64"])[0] ^ 1]) + base64.b64decode(b["decision"]["sig_b64"])[1:]
    ).decode()), "decision signature"),
    ("key", lambda b: b.__setitem__("public_key_b64", base64.b64encode(va.encode_public_key(va.mul(5, va.G))).decode()),
     "decision signature"),
    ("digest", lambda b: b.__setitem__("decision_digest", ZERO_DIGEST), "decision digest"),
    ("params", lambda b: b["sprt_params"].__setitem__("beta", 0.06), "params digest"),
    ("envelope", lambda b: b["ledger"][1].__setitem__("raw_digest", None), "ledger replay"),
    ("prefix", lambda b: b.__setitem__("ledger", b["ledger"][:1]), "ledger replay"),
    ("anchor", lambda b: b["anchor"]["inclusion"]["path"].__setitem__(0, "ff" * 32), "anchor inclusion"),
])
def test_tampering_with_a_kms_bundle_is_caught(world: dict[str, Any], name: str, mutate: Any, failing: str) -> None:
    mutate(world)
    failed = [c.name for c in va.verify_bundle(world) if not c.ok]
    assert failing in failed, (name, failed)


def test_a_record_signed_by_someone_else_does_not_pass_as_ours(world: dict[str, Any], signer: KmsSigner,
                                                              kms: Any) -> None:
    other = KmsSigner(kms, kms.create_key(KeySpec="ECC_NIST_P256", KeyUsage="SIGN_VERIFY")["KeyMetadata"]["KeyId"])
    world["decision"] = sign.attest_decision(record(), other)
    assert not next(c for c in va.verify_bundle(world) if c.name == "decision signature").ok


def test_the_signed_bytes_are_never_re_serialized(world: dict[str, Any]) -> None:
    """Pretty-printing the same record changes the bytes, so the original signature must not cover it."""
    pretty = json.dumps(json.loads(base64.b64decode(world["decision"]["bytes_b64"])), indent=2).encode()
    world["decision"]["bytes_b64"] = base64.b64encode(pretty).decode()
    assert not next(c for c in va.verify_bundle(world) if c.name == "decision signature").ok


def test_an_anchor_ahead_of_the_supplied_ledger_is_noted_not_failed(signer: KmsSigner, spec: dict[str, Any],
                                                                   orrery_runs: dict[str, orrery.Result]) -> None:
    envelopes = orrery_runs["doorstep_scam"].envelopes
    bundle = bundle_for(signer, spec["sprt"], envelopes, at_seq=3)
    bundle["ledger"] = envelopes[:6]
    checks = va.verify_bundle(bundle)
    assert next(c for c in checks if c.name == "anchor head").detail.startswith("anchor is newer")
    assert all(c.ok for c in checks)


def test_genesis_decision(signer: KmsSigner, spec: dict[str, Any], orrery_runs: dict[str, orrery.Result]) -> None:
    envelopes = orrery_runs["doorstep_scam"].envelopes
    rec = record(at_seq=0, belief_seq=0, ledger_head=ZERO_DIGEST, params_digest=sign.params_digest(spec["sprt"]))
    bundle = {"v": 1, "public_key_b64": base64.b64encode(signer.public_key_der()).decode(),
              "decision": sign.attest_decision(rec, signer), "ledger": copy.deepcopy(envelopes)}
    assert all(c.ok for c in va.verify_bundle(bundle))


# ── anchors ─────────────────────────────────────────────────────────────────────────────────────────────


def leaves_for(n: int) -> list[bytes]:
    return [anchor_mod.leaf_hash(f"hh_{i:08d}", i + 1, "sha256:" + f"{i:064x}") for i in range(n)]


@pytest.mark.parametrize("n", list(range(1, 18)))
def test_the_two_merkle_implementations_agree_for_every_small_size(n: int) -> None:
    leaves = leaves_for(n)
    assert anchor_mod.merkle_root(leaves) == va.merkle(leaves)
    for i in range(n):
        path = anchor_mod.audit_path(leaves, i)
        assert anchor_mod.root_from_path(leaves[i], i, n, path) == anchor_mod.merkle_root(leaves)
        assert va.path_root(leaves[i], i, n, path) == anchor_mod.merkle_root(leaves)
        if n > 1:
            assert anchor_mod.root_from_path(leaves[i], i, n, path[:-1]) is None
            assert anchor_mod.root_from_path(leaves[i], (i + 1) % n, n, path) != anchor_mod.merkle_root(leaves)


def test_leaves_and_nodes_cannot_be_confused() -> None:
    leaf = anchor_mod.leaf_hash("hh_aaaaaaaa", 1, "sha256:" + "0" * 64)
    node = anchor_mod._hash(anchor_mod.NODE, leaf, leaf)
    assert leaf != node and anchor_mod.merkle_root([leaf, leaf]) == node
    assert anchor_mod.merkle_root([leaf]) == leaf


def test_the_anchor_is_sorted_deterministic_and_signed(signer: KmsSigner) -> None:
    heads = {"hh_zzzzzzzz": (2, "sha256:" + "22" * 32), "hh_aaaaaaaa": (1, "sha256:" + "11" * 32)}
    doc = anchor_mod.build_anchor(heads, 1_800_000_000_000)
    assert [leaf["household"] for leaf in doc["leaves"]] == ["hh_aaaaaaaa", "hh_zzzzzzzz"] and doc["count"] == 2
    assert doc == anchor_mod.build_anchor(dict(reversed(list(heads.items()))), 1_800_000_000_000)
    assert doc["root"] != anchor_mod.build_anchor({**heads, "hh_aaaaaaaa": (1, "sha256:" + "33" * 32)}, 1)["root"]
    signed = anchor_mod.sign_anchor(doc, signer)
    assert base64.b64decode(signed["bytes_b64"]) == anchor_mod.anchor_bytes(doc)
    assert anchor_mod.anchor_digest(anchor_mod.anchor_bytes(doc)).startswith("sha256:")
    with pytest.raises(ValueError):
        anchor_mod.build_anchor({}, 1)
    with pytest.raises(ValueError):
        anchor_mod.build_anchor(heads, 1.5)  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        anchor_mod.inclusion(doc, "hh_missing01")


def test_the_object_key_is_dated_and_unique_per_anchor() -> None:
    assert anchor_mod.object_key(1_800_000_000_000) == "anchors/2027/01/15/1800000000000.json"


@pytest.fixture
def lock_bucket(aws: None) -> Any:
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET, ObjectLockEnabledForBucket=True)
    return s3


def test_anchors_are_written_in_compliance_mode_and_never_overwritten(lock_bucket: Any, signer: KmsSigner) -> None:
    doc = anchor_mod.build_anchor({"hh_aaaaaaaa": (1, "sha256:" + "11" * 32)}, 1_800_000_000_000)
    signed = anchor_mod.sign_anchor(doc, signer)
    writer = anchor_mod.AnchorWriter(lock_bucket, BUCKET, retention_days=30)
    now = datetime(2026, 10, 21, tzinfo=UTC)
    key = writer.put(doc, signed, now)
    stored = json.loads(lock_bucket.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    assert stored == {"anchor": doc, "attestation": signed}
    head = lock_bucket.head_object(Bucket=BUCKET, Key=key)  # moto's GetObjectRetention is unreliable; HEAD is not
    assert head["ObjectLockMode"] == "COMPLIANCE"
    assert head["ObjectLockRetainUntilDate"].date() == datetime(2026, 11, 20).date()
    with pytest.raises(Exception):  # noqa: B017 - a second write to the same key must fail
        writer.put(doc, signed, now)
    assert json.loads(lock_bucket.get_object(Bucket=BUCKET, Key=key)["Body"].read())["anchor"] == doc


def test_an_anchor_with_foreign_signed_bytes_is_not_written(lock_bucket: Any, signer: KmsSigner) -> None:
    doc = anchor_mod.build_anchor({"hh_aaaaaaaa": (1, "sha256:" + "11" * 32)}, 1)
    other = anchor_mod.build_anchor({"hh_bbbbbbbb": (1, "sha256:" + "11" * 32)}, 1)
    with pytest.raises(ValueError):
        anchor_mod.AnchorWriter(lock_bucket, BUCKET).put(doc, anchor_mod.sign_anchor(other, signer))
    with pytest.raises(ValueError):
        anchor_mod.AnchorWriter(lock_bucket, BUCKET, retention_days=0)
    assert "Contents" not in lock_bucket.list_objects_v2(Bucket=BUCKET)


# ── the gate variable ──────────────────────────────────────────────────────────────────────────────────────


def test_the_anchor_bucket_variable_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pipeline.GATE_ENV["G6"] == ["HW_ANCHOR_BUCKET"]
    assert pipeline.ENV_RULES["HW_ANCHOR_BUCKET"](BUCKET) is None
    monkeypatch.setenv("HW_ANCHOR_BUCKET", BUCKET)
    assert pipeline._env("G6")(pipeline.Context(probe=False)).status == pipeline.PASS
    for bad in ("Bad_Bucket", "ab", "-lead", "UPPER"):
        assert pipeline.ENV_RULES["HW_ANCHOR_BUCKET"](bad) is not None


def test_the_env_example_names_it() -> None:
    assert "\nHW_ANCHOR_BUCKET=" in (ROOT / ".env.example").read_text(encoding="utf-8")


# ── the web app ─────────────────────────────────────────────────────────────────────────────────────────


def test_the_lockfile_is_present_tracked_and_has_no_dependencies() -> None:
    lock = json.loads((WEB / "package-lock.json").read_text(encoding="utf-8"))
    package = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    assert lock["lockfileVersion"] == 3 and lock["name"] == package["name"] and lock["version"] == package["version"]
    assert set(lock["packages"]) == {""} and "dependencies" not in package and "devDependencies" not in package
    assert package["private"] is True and package["type"] == "module" and package["scripts"]["test"] == "node --test"
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "web/package-lock.json"], cwd=ROOT,
                             capture_output=True, text=True, check=False)
    assert tracked.returncode == 0, "web/package-lock.json must be tracked (git add it)"


@pytest.mark.parametrize("page", ["approve.html", "verify.html"])
def test_pages_have_a_strict_csp_and_no_inline_script_or_remote_resource(page: str) -> None:
    html = (WEB / page).read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', html)
    assert csp is not None and "default-src 'none'" in csp.group(1) and "script-src 'self'" in csp.group(1)
    assert "unsafe-inline" not in csp.group(1) and "unsafe-eval" not in csp.group(1)
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html) and "onclick" not in html and "http://" not in html
    assert not re.search(r"(?:src|href)=\"https?://", html)
    for src in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert (WEB / src).is_file(), src


def test_the_web_sources_only_call_the_brokers_api_and_never_inject_unescaped_html() -> None:
    glue = {"verify_page.js", "approve_page.js"}
    for path in (WEB / "src").glob("*.js"):
        lines = path.read_text(encoding="utf-8").splitlines()
        code = "\n".join(line for line in lines if not line.strip().startswith("//"))
        assert "eval(" not in code and "document.write" not in code, path.name
        for url in re.findall(r"fetch\(`?([^,)`]+)", code):
            assert url.startswith("/api/"), (path.name, url)
        for line in code.splitlines():
            if ".innerHTML" in line:
                assert path.name in glue, (path.name, line)
            if ".innerHTML =" in line or line.strip().startswith(("`<h2", "renderChecks(")):
                assert "render" in line or "escapeHtml" in line, (path.name, line)


def test_the_web_unit_tests_pass() -> None:
    assert NODE and NPM, "node and npm are required to test the web app"
    proc = subprocess.run([NPM, "test", "--silent"], cwd=WEB, capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace", check=False)
    assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-500:]
    assert re.search(r"ℹ pass (\d+)", proc.stdout) and "ℹ fail 0" in proc.stdout


NODE_CHECK = """
import { pathToFileURL } from 'node:url';
import { readFileSync } from 'node:fs';
const { verifyBundle, actionHash } = await import(pathToFileURL(process.env.WEB_ATTEST).href);
const { bytesToHex } = await import(pathToFileURL(process.env.WEB_ENCODING).href);
const mode = process.env.MODE;
if (mode === 'bundle') {
  const result = await verifyBundle(JSON.parse(readFileSync(process.env.BUNDLE, 'utf8')));
  console.log(JSON.stringify({ ok: result.ok, failed: result.checks.filter((c) => !c.ok).map((c) => c.name) }));
} else {
  console.log(bytesToHex(await actionHash(JSON.parse(process.env.DOC))));
}
"""


def node(mode: str, **env: str) -> str:
    assert NODE, "node is required"
    proc = subprocess.run([NODE, "--input-type=module", "-e", NODE_CHECK], capture_output=True, text=True, timeout=60,
                          env={**__import__("os").environ, "MODE": mode, "WEB_ATTEST": str(WEB / "src/attest.js"),
                               "WEB_ENCODING": str(WEB / "src/encoding.js"), **env}, check=False)
    assert proc.returncode == 0, proc.stderr[-1000:]
    return proc.stdout.strip()


def test_the_browser_verifier_accepts_a_kms_bundle_and_rejects_tampering(world: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(world), encoding="utf-8")
    assert json.loads(node("bundle", BUNDLE=str(path))) == {"ok": True, "failed": []}
    for mutate, failing in ((lambda b: b["ledger"][2].__setitem__("received_at", 7), "ledger replay"),
                            (lambda b: b["decision"].__setitem__("sig_b64", base64.b64encode(b"\x30\x06\x02\x01\x01\x02\x01\x01"
                                                                                           ).decode()), "decision signature"),
                            (lambda b: b["anchor"]["inclusion"]["path"].__setitem__(0, "aa" * 32), "anchor inclusion")):
        bad = copy.deepcopy(world)
        mutate(bad)
        path.write_text(json.dumps(bad), encoding="utf-8")
        result = json.loads(node("bundle", BUNDLE=str(path)))
        assert result["ok"] is False and failing in result["failed"], result


def test_the_python_and_javascript_action_hashes_are_equal() -> None:
    doc = {"tool": "pause_monitoring", "args": {"hours": 4, "reason": "travel"}, "household": "hh_synthetic",
           "episode_version": 3, "nonce": "ab" * 16, "exp": 1_800_000_300_000}
    expected = action_hash(doc["tool"], doc["args"], doc["household"], doc["episode_version"], doc["nonce"], doc["exp"])
    assert node("action", DOC=json.dumps(doc)) == expected.hex()
