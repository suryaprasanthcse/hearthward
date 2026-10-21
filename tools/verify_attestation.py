#!/usr/bin/env python3
"""Offline judge verification of a HEARTHWARD decision attestation. Standard library only; no network, no AWS.

    python tools/verify_attestation.py bundle.json        verify a bundle (exit 0 only if every check passes)
    python tools/verify_attestation.py --selftest         known-answer tests, a synthetic bundle, and tampering

A bundle is JSON:

    {"v": 1,
     "public_key_b64": "<SubjectPublicKeyInfo DER of the attest-signer key, base64>",
     "decision": {"bytes_b64": ..., "sig_b64": ..., "key_id": ...},        the signed canonical bytes (C-6)
     "decision_digest": "sha256:..."                                      optional: the digest being claimed
     "sprt_params": {...}                                                  optional: the hearthward:sprt block
     "ledger": [envelope, ...]                                             the household's envelopes from seq 1
     "anchor": {"attestation": {...}, "inclusion": {"index": 0, "size": 1, "path": ["hex", ...]}}   optional
    }

What it checks, in this order (blueprint section 9 and rule C-6):

    1. the ECDSA P-256 signature over the *raw signed bytes*, before anything is parsed
    2. the bytes parse as strict HW-JCS and hold a decision record
    3. the decision digest: SHA-256("hw-decision-v1" || 0x00 || bytes) (and equals the claimed digest, if given)
    4. params_digest equals the SHA-256 of the sprt block as sorted compact JSON (if the block is supplied)
    5. the ledger chain is intact from the genesis head sha256:000...0, and holds the recorded head at at_seq
    6. the anchor: its signature, its Merkle root, and the household's inclusion path

Signature bytes are never re-serialized. Only ledger heads recompute JSON, because the chain is defined over it.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# ── NIST P-256 (secp256r1) ─────────────────────────────────────────────────────────────────────────────

P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
     0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)
Point = tuple[int, int] | None


def on_curve(pt: tuple[int, int]) -> bool:
    x, y = pt
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def add(p: Point, q: Point) -> Point:
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0]:
        if (p[1] + q[1]) % P == 0:
            return None
        lam = (3 * p[0] * p[0] + A) * pow(2 * p[1], -1, P) % P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], -1, P) % P
    x = (lam * lam - p[0] - q[0]) % P
    return x, (lam * (p[0] - x) - p[1]) % P


def mul(k: int, p: Point) -> Point:
    result: Point = None
    while k:
        if k & 1:
            result = add(result, p)
        p = add(p, p)
        k >>= 1
    return result


def ecdsa_verify(pub: tuple[int, int], message: bytes, r: int, s: int) -> bool:
    if not on_curve(pub) or not (1 <= r < N and 1 <= s < N):
        return False
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    w = pow(s, -1, N)
    point = add(mul(z * w % N, G), mul(r * w % N, pub))
    return point is not None and point[0] % N == r


def ecdsa_sign(priv: int, message: bytes, k: int | None = None) -> tuple[int, int]:
    """For the self-test only: deterministic and NOT RFC 6979 unless `k` is given. Never use for real keys."""
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    k = k or int.from_bytes(hashlib.sha256(priv.to_bytes(32, "big") + message).digest(), "big") % (N - 1) + 1
    r = mul(k, G)[0] % N  # type: ignore[index]
    return r, pow(k, -1, N) * (z + r * priv) % N


# ── strict DER ────────────────────────────────────────────────────────────────────────────────────────

SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")  # id-ecPublicKey, prime256v1


class VerifyError(Exception):
    pass


def _tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    if pos + 2 > len(data):
        raise VerifyError("DER: truncated")
    tag, first = data[pos], data[pos + 1]
    pos += 2
    if first < 0x80:
        length = first
    else:
        count = first & 0x7F
        if count == 0 or count > 2 or pos + count > len(data):
            raise VerifyError("DER: bad length")
        length = int.from_bytes(data[pos:pos + count], "big")
        pos += count
        if length < 0x80 or (count == 2 and length < 0x100):
            raise VerifyError("DER: non-minimal length")
    if pos + length > len(data):
        raise VerifyError("DER: truncated")
    return tag, data[pos:pos + length], pos + length


def _der_int(body: bytes) -> int:
    if not body or (body[0] & 0x80) or (len(body) > 1 and body[0] == 0 and not body[1] & 0x80):
        raise VerifyError("DER: not a minimal non-negative integer")
    return int.from_bytes(body, "big")


def parse_signature(der: bytes) -> tuple[int, int]:
    tag, body, end = _tlv(der, 0)
    if tag != 0x30 or end != len(der):
        raise VerifyError("signature is not one DER SEQUENCE")
    t1, r, pos = _tlv(body, 0)
    t2, s, pos = _tlv(body, pos)
    if t1 != 0x02 or t2 != 0x02 or pos != len(body):
        raise VerifyError("signature is not SEQUENCE { INTEGER, INTEGER }")
    return _der_int(r), _der_int(s)


def parse_public_key(der: bytes) -> tuple[int, int]:
    if len(der) != 91 or der[:26] != SPKI_PREFIX or der[26] != 4:
        raise VerifyError("public key is not an uncompressed P-256 SubjectPublicKeyInfo")
    point = (int.from_bytes(der[27:59], "big"), int.from_bytes(der[59:91], "big"))
    if not on_curve(point):
        raise VerifyError("public key is not on the P-256 curve")
    return point


def encode_signature(r: int, s: int) -> bytes:
    def integer(v: int) -> bytes:
        raw = v.to_bytes(32, "big").lstrip(b"\x00") or b"\x00"
        raw = b"\x00" + raw if raw[0] & 0x80 else raw
        return b"\x02" + bytes([len(raw)]) + raw

    body = integer(r) + integer(s)
    return b"\x30" + bytes([len(body)]) + body


def encode_public_key(pt: tuple[int, int]) -> bytes:
    return SPKI_PREFIX + b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


# ── HW-JCS, strictly (C-2) ────────────────────────────────────────────────────────────────────────────────

SAFE_INT = 2**53 - 1
KEY = re.compile(r"[a-z0-9_]+")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
ZERO_DIGEST = "sha256:" + "0" * 64


def check_profile(value: Any, path: str = "$", depth: int = 0) -> None:
    if depth > 64:
        raise VerifyError(f"{path}: nested too deeply")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > SAFE_INT:
            raise VerifyError(f"{path}: integer outside 2**53 - 1")
    elif isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise VerifyError(f"{path}: string is not valid Unicode") from None
    elif isinstance(value, list):
        for i, item in enumerate(value):
            check_profile(item, f"{path}[{i}]", depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not KEY.fullmatch(key):
                raise VerifyError(f"{path}: key {key!r} is outside [a-z0-9_]+")
            check_profile(item, f"{path}.{key}", depth + 1)
    else:
        raise VerifyError(f"{path}: {type(value).__name__} is outside the profile (no floats)")


def jcs(value: Any) -> bytes:
    """RFC 8785 bytes. Inside the C-2 profile these are exactly sorted, compact, non-ASCII-escaped JSON."""
    check_profile(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise VerifyError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise VerifyError(f"{name} is not allowed")


def _no_float(text: str) -> Any:
    raise VerifyError(f"float {text} is outside the profile")


def parse_strict(raw: bytes) -> Any:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constant,
                           parse_float=_no_float)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise VerifyError(f"not valid JSON: {exc}") from None
    check_profile(value)
    return value


def tagged(tag: bytes, data: bytes) -> str:
    return "sha256:" + hashlib.sha256(tag + b"\x00" + data).hexdigest()


# ── the checks ────────────────────────────────────────────────────────────────────────────────────────────

DECISION_FIELDS = {"episode", "decision", "at_seq", "ledger_head", "belief_seq", "lambda_mnats", "reasons",
                   "thresholds_mnats", "params_digest", "extractor_model_ids", "cross_terms"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def b64(text: Any, what: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise VerifyError(f"{what} is not base64") from None


def check_signature(public_key: tuple[int, int], signed: dict[str, Any], what: str) -> tuple[Check, bytes | None]:
    """Rule C-6: check the signature over the raw bytes first; nothing has been parsed yet."""
    try:
        data, sig = b64(signed["bytes_b64"], "bytes_b64"), b64(signed["sig_b64"], "sig_b64")
        r, s = parse_signature(sig)
        ok = ecdsa_verify(public_key, data, r, s)
    except (VerifyError, KeyError, TypeError) as exc:
        return Check(f"{what} signature", False, f"unreadable: {exc}"), None
    key_id = signed.get("key_id", "?")
    return Check(f"{what} signature", ok, f"ECDSA P-256 over {len(data)} raw bytes (key_id {key_id})"
                 if ok else "the signature does not verify against the public key"), data if ok else None


def replay_ledger(ledger: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Problems with the chain from genesis (E-2, E-3 and every head), and the envelopes."""
    problems: list[str] = []
    if not isinstance(ledger, list) or not ledger:
        return ["the bundle holds no ledger"], []
    prev, household = ZERO_DIGEST, None
    for n, env in enumerate(ledger, 1):
        if not isinstance(env, dict) or not all(k in env for k in ("seq", "prev", "head")):
            problems.append(f"position {n}: not an envelope with seq, prev and head")
            return problems, []
        if env["seq"] != n or type(env["seq"]) is not int:
            problems.append(f"position {n}: seq is {env['seq']!r}")
        if env["prev"] != prev:
            problems.append(f"seq {n}: prev is not the previous head")
        try:
            body = {k: v for k, v in env.items() if k != "head"}
            head = tagged(b"hw-ledger-v1", jcs(body))
        except VerifyError as exc:
            problems.append(f"seq {n}: {exc}")
            head = None
        if head != env["head"]:
            problems.append(f"seq {n}: head does not match its contents")
        household = household or env.get("household")
        if env.get("household") != household:
            problems.append(f"seq {n}: belongs to another household")
        prev = env["head"]
    return problems, ledger


def merkle(leaves: Sequence[bytes]) -> bytes:
    tag = b"hw-anchor-v1\x00"
    if len(leaves) == 1:
        return leaves[0]
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    return hashlib.sha256(tag + b"\x01" + merkle(leaves[:k]) + merkle(leaves[k:])).digest()


def leaf_hash(leaf: dict[str, Any]) -> bytes:
    data = jcs({"household": leaf["household"], "seq": leaf["seq"], "head": leaf["head"]})
    return hashlib.sha256(b"hw-anchor-v1\x00\x00" + data).digest()


def path_root(leaf: bytes, index: int, size: int, path: Sequence[bytes]) -> bytes | None:
    tag = b"hw-anchor-v1\x00"
    if not 0 <= index < size:
        return None
    if size == 1:
        return leaf if not path else None
    if not path:
        return None
    k = 1
    while k * 2 < size:
        k *= 2
    if index < k:
        sub = path_root(leaf, index, k, path[:-1])
        return None if sub is None else hashlib.sha256(tag + b"\x01" + sub + path[-1]).digest()
    sub = path_root(leaf, index - k, size - k, path[:-1])
    return None if sub is None else hashlib.sha256(tag + b"\x01" + path[-1] + sub).digest()


def check_anchor(public_key: tuple[int, int], anchor: dict[str, Any], household: str | None,
                 ledger: list[dict[str, Any]]) -> list[Check]:
    sig_check, data = check_signature(public_key, anchor.get("attestation", {}), "anchor")
    checks = [sig_check]
    if data is None:
        return checks
    try:
        doc = parse_strict(data)
        leaves = doc["leaves"]
        recomputed = "sha256:" + merkle([leaf_hash(leaf) for leaf in leaves]).hex()
        root_ok = recomputed == doc["root"] and doc["count"] == len(leaves)
        checks.append(Check("anchor root", root_ok, f"{len(leaves)} households under {doc['root'][:19]}..."
                            if root_ok else "the leaves do not hash to the signed root"))
        inc = anchor["inclusion"]
        mine = next((leaf for leaf in leaves if leaf["household"] == household), None)
        if mine is None:
            checks.append(Check("anchor inclusion", False, f"{household} is not among the anchored households"))
            return checks
        path = [bytes.fromhex(p) for p in inc["path"]]
        got = path_root(leaf_hash(mine), inc["index"], inc["size"], path)
        index_ok = leaves[inc["index"]] == mine if 0 <= inc["index"] < len(leaves) else False
        ok = got is not None and "sha256:" + got.hex() == doc["root"] and inc["size"] == len(leaves) and index_ok
        checks.append(Check("anchor inclusion", ok, f"{household} is leaf {inc['index']} of {inc['size']}"
                            if ok else "the audit path does not lead to the signed root"))
        if mine["seq"] <= len(ledger):
            same = ledger[mine["seq"] - 1]["head"] == mine["head"]
            checks.append(Check("anchor head", same, f"anchored head at seq {mine['seq']} is in the replayed ledger"
                                if same else "the anchored head differs from the replayed ledger"))
        else:
            checks.append(Check("anchor head", True, f"anchor is newer (seq {mine['seq']}) than the supplied ledger; "
                                "not compared"))
    except (VerifyError, KeyError, TypeError, ValueError, IndexError) as exc:
        checks.append(Check("anchor document", False, f"unreadable: {exc}"))
    return checks


def verify_bundle(bundle: Any) -> list[Check]:
    checks: list[Check] = []
    try:
        public_key = parse_public_key(b64(bundle["public_key_b64"], "public_key_b64"))
    except (VerifyError, KeyError, TypeError) as exc:
        return [Check("public key", False, str(exc))]
    fingerprint = hashlib.sha256(b64(bundle["public_key_b64"], "key")).hexdigest()
    checks.append(Check("public key", True, f"P-256, SPKI SHA-256 {fingerprint[:16]}... (compare with the published key)"))

    sig_check, data = check_signature(public_key, bundle.get("decision", {}), "decision")
    checks.append(sig_check)
    if data is None:
        return checks
    try:
        record = parse_strict(data)
        if not isinstance(record, dict) or set(record) != DECISION_FIELDS:
            raise VerifyError("not a decision record (wrong fields)")
        checks.append(Check("decision record", True, f"episode {record['episode']}, {record['decision']}, "
                                                     f"lambda {record['lambda_mnats']} mnats at seq {record['at_seq']}"))
    except VerifyError as exc:
        checks.append(Check("decision record", False, str(exc)))
        return checks

    digest = tagged(b"hw-decision-v1", data)
    claimed = bundle.get("decision_digest")
    checks.append(Check("decision digest", claimed in (None, digest),
                        f"{digest} (hw-decision-v1)" if claimed in (None, digest) else
                        f"recomputed {digest}, but the bundle claims {claimed}"))

    if "sprt_params" in bundle:
        text = json.dumps(bundle["sprt_params"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        want = "sha256:" + hashlib.sha256(text.encode("ascii")).hexdigest()
        checks.append(Check("params digest", want == record["params_digest"],
                            "the sprt block hashes to params_digest" if want == record["params_digest"]
                            else f"the sprt block hashes to {want}, the record says {record['params_digest']}"))

    problems, ledger = replay_ledger(bundle.get("ledger"))
    at_seq = record["at_seq"]
    if not problems:
        recorded = ZERO_DIGEST if at_seq == 0 else (ledger[at_seq - 1]["head"] if 0 < at_seq <= len(ledger) else None)
        if recorded is None:
            problems.append(f"at_seq {at_seq} is beyond the supplied ledger ({len(ledger)} envelopes)")
        elif recorded != record["ledger_head"]:
            problems.append(f"the head at seq {at_seq} is {recorded}, the decision recorded {record['ledger_head']}")
    checks.append(Check("ledger replay", not problems,
                        f"{len(ledger)} envelopes chain from the genesis head to {record['ledger_head'][:19]}... "
                        f"at seq {at_seq}" if not problems else "; ".join(problems[:3])))

    if "anchor" in bundle:
        household = ledger[0].get("household") if ledger else None
        checks += check_anchor(public_key, bundle["anchor"], household, ledger)
    return checks


# ── self-test ────────────────────────────────────────────────────────────────────────────────────────────


def selftest_bundle() -> dict[str, Any]:
    """A synthetic world: a six-envelope ledger, a signed decision, and a three-household anchor."""
    priv = int.from_bytes(hashlib.sha256(b"hearthward selftest key (synthetic, public)").digest(), "big") % N or 1
    pub = mul(priv, G)
    ledger, prev = [], ZERO_DIGEST
    for n in range(1, 7):
        env = {"v": 1, "household": "hh_selftest01", "source": "system", "source_event_id": f"sys:selftest{n}",
               "kind": "room.concern", "valid": {"lo": 1_000 * n, "hi": 1_000 * n}, "src_time": None,
               "sent_time": None, "received_at": 1_000 * n, "seq": n, "raw_digest": None,
               "payload_digest": "sha256:" + hashlib.sha256(str(n).encode()).hexdigest(),
               "auth": {"scheme": "internal", "verified": True, "synthetic": True}, "prev": prev}
        env["head"] = tagged(b"hw-ledger-v1", jcs(env))
        ledger.append(env)
        prev = env["head"]
    sprt = {"alpha": 0.01, "beta": 0.05, "thresholds_reported": {"A": 4.554, "B": -2.986}}
    params = "sha256:" + hashlib.sha256(json.dumps(sprt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    record = {"episode": "ep_" + "0123456789ABCDEFGHJKMNPQRS", "decision": "INTERVENE", "at_seq": 5,
              "ledger_head": ledger[4]["head"], "belief_seq": 5, "lambda_mnats": 5123, "reasons": ["SECRECY_REQUEST"],
              "thresholds_mnats": {"a": 4554, "b": -2986}, "params_digest": params,
              "extractor_model_ids": ["synthetic"], "cross_terms": ["door_unknown_overlap"]}
    data = jcs(record)

    def sign(payload: bytes) -> dict[str, str]:
        r, s = ecdsa_sign(priv, payload)
        return {"bytes_b64": base64.b64encode(payload).decode(), "sig_b64": base64.b64encode(
            encode_signature(r, s)).decode(), "key_id": "selftest-key"}

    heads = [{"household": "hh_aaaaaaaa", "seq": 3, "head": tagged(b"x", b"1")},
             {"household": "hh_selftest01", "seq": 6, "head": ledger[5]["head"]},
             {"household": "hh_zzzzzzzz", "seq": 9, "head": tagged(b"x", b"2")}]
    hashes = [leaf_hash(leaf) for leaf in heads]
    root = "sha256:" + merkle(hashes).hex()
    document = {"v": 1, "at_ms": 1_800_000_000_000, "count": 3, "root": root, "leaves": heads}
    path = [bytes.fromhex(p) for p in _path_for(hashes, 1)]
    assert path_root(hashes[1], 1, 3, path) == merkle(hashes)
    return {"v": 1, "public_key_b64": base64.b64encode(encode_public_key(pub)).decode(), "decision": sign(data),
            "decision_digest": tagged(b"hw-decision-v1", data), "sprt_params": sprt, "ledger": ledger,
            "anchor": {"attestation": sign(jcs(document)),
                       "inclusion": {"index": 1, "size": 3, "path": [p.hex() for p in path]}}}


def _path_for(leaves: Sequence[bytes], index: int) -> list[str]:
    tag = b"hw-anchor-v1\x00"
    if len(leaves) == 1:
        return []
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    if index < k:
        return [*_path_for(leaves[:k], index), merkle(leaves[k:]).hex()]
    return [*_path_for(leaves[k:], index - k), merkle(leaves[:k]).hex()] if tag else []


def selftest() -> int:
    results: list[tuple[str, bool]] = []

    def record(name: str, ok: bool) -> None:
        results.append((name, bool(ok)))
        print(f"  {'ok  ' if ok else 'FAIL'}  {name}")

    # known answers: RFC 6979 appendix A.2.5 (P-256, SHA-256, message "sample")
    x = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
    pub = mul(x, G)
    record("P-256 public key from the RFC 6979 A.2.5 private key",
           pub == (0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6,
                   0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299))
    r = 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716
    s = 0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8
    record("RFC 6979 A.2.5 signature verifies", ecdsa_verify(pub, b"sample", r, s))
    record("RFC 6979 signature fails for another message", not ecdsa_verify(pub, b"Sample", r, s))
    record("RFC 6979 signature fails with s changed", not ecdsa_verify(pub, b"sample", r, s ^ 1))
    record("RFC 6979 signature fails with r = 0 or s = N",
           not ecdsa_verify(pub, b"sample", 0, s) and not ecdsa_verify(pub, b"sample", r, N))
    record("DER round trip of that signature", parse_signature(encode_signature(r, s)) == (r, s))
    record("SPKI round trip of that key", parse_public_key(encode_public_key(pub)) == pub)
    record("a key off the curve is refused", _raises(lambda: parse_public_key(SPKI_PREFIX + b"\x04" + b"\x01" * 64)))
    record("a non-minimal DER integer is refused",
           _raises(lambda: parse_signature(bytes.fromhex("3008020200010202" + "0001"))))
    record("JCS sorts keys and escapes like RFC 8785",
           jcs({"b": 1, "a": [True, None, "x\né"]}) == '{"a":[true,null,"x\\né"],"b":1}'.encode())
    for bad, why in (({"a": 1.5}, "a float"), ({"A": 1}, "an upper-case key"), ({"a": 2**53}, "a huge integer"),
                     ({"a": float("nan")}, "NaN")):
        record(f"HW-JCS refuses {why}", _raises(lambda bad=bad: jcs(bad)))
    record("duplicate keys are refused when parsing", _raises(lambda: parse_strict(b'{"a":1,"a":2}')))
    record("a float is refused when parsing", _raises(lambda: parse_strict(b'{"a":1.0}')))

    bundle = selftest_bundle()
    good = verify_bundle(bundle)
    for check in good:
        record(f"synthetic bundle: {check.name}", check.ok)
    record("synthetic bundle passes every check", all(c.ok for c in good) and len(good) >= 9)

    def tampered(mutate: Any) -> list[Check]:
        copy = json.loads(json.dumps(bundle))
        mutate(copy)
        return verify_bundle(copy)

    def flips(b64text: str, at: int) -> str:
        raw = bytearray(base64.b64decode(b64text))
        raw[at] ^= 1
        return base64.b64encode(bytes(raw)).decode()

    cases: list[tuple[str, Any, str]] = [
        ("a changed byte in the signed record", lambda b: b["decision"].__setitem__(
            "bytes_b64", flips(b["decision"]["bytes_b64"], 10)), "decision signature"),
        ("a changed signature", lambda b: b["decision"].__setitem__(
            "sig_b64", flips(b["decision"]["sig_b64"], 12)), "decision signature"),
        ("another public key", lambda b: b.__setitem__("public_key_b64", base64.b64encode(
            encode_public_key(mul(7, G))).decode()), "decision signature"),
        ("a wrong claimed digest", lambda b: b.__setitem__("decision_digest", ZERO_DIGEST), "decision digest"),
        ("a different sprt block", lambda b: b["sprt_params"].__setitem__("alpha", 0.02), "params digest"),
        ("an edited envelope", lambda b: b["ledger"][2].__setitem__("received_at", 1), "ledger replay"),
        ("a dropped envelope", lambda b: b["ledger"].pop(1), "ledger replay"),
        ("swapped envelopes", lambda b: b["ledger"].insert(0, b["ledger"].pop(1)), "ledger replay"),
        ("a ledger that stops before the decision", lambda b: b.__setitem__("ledger", b["ledger"][:3]),
         "ledger replay"),
        ("a float in an envelope", lambda b: b["ledger"][0].__setitem__("received_at", 1.5), "ledger replay"),
        ("no ledger at all", lambda b: b.pop("ledger"), "ledger replay"),
        ("a changed anchor document", lambda b: b["anchor"]["attestation"].__setitem__(
            "bytes_b64", flips(b["anchor"]["attestation"]["bytes_b64"], 20)), "anchor signature"),
        ("a wrong audit path", lambda b: b["anchor"]["inclusion"]["path"].__setitem__(0, "00" * 32),
         "anchor inclusion"),
        ("a wrong leaf index", lambda b: b["anchor"]["inclusion"].__setitem__("index", 0), "anchor inclusion"),
    ]
    for name, mutate, expected in cases:
        failed = [c.name for c in tampered(mutate) if not c.ok]
        record(f"tampering is caught: {name}", expected in failed)

    passed = sum(ok for _, ok in results)
    print(f"selftest: {passed}/{len(results)} checks passed" if passed == len(results)
          else f"selftest FAILED: {passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


def _raises(call: Any) -> bool:
    try:
        call()
    except (VerifyError, ValueError):
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline verification of a HEARTHWARD decision attestation.")
    parser.add_argument("bundle", nargs="?", help="path to a bundle JSON file")
    parser.add_argument("--selftest", action="store_true", help="run the built-in known-answer and tamper tests")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.bundle:
        parser.error("give a bundle file, or --selftest")
    try:
        with open(args.bundle, "rb") as handle:
            bundle = json.loads(handle.read())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read the bundle: {exc}", file=sys.stderr)
        return 2
    checks = verify_bundle(bundle)
    for check in checks:
        print(f"{'PASS' if check.ok else 'FAIL'}  {check.name}: {check.detail}")
    good = all(c.ok for c in checks)
    print("VERIFIED" if good else "NOT VERIFIED")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
