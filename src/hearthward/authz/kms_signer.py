"""ES256 signing with the `facade-token-signer` KMS key (blueprint §7.2): the private key never leaves KMS."""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import load_der_public_key

from hearthward.authz.facade_as import b64url


class KmsEs256Signer:
    """`client` is a boto3 KMS client; `key_id` a key id, ARN or alias for an ECC_NIST_P256 SIGN_VERIFY key."""

    def __init__(self, client: Any, key_id: str, kid: str | None = None) -> None:
        self._client, self._key_id = client, key_id
        self.kid = kid or key_id.rsplit("/", 1)[-1]
        self._jwk: dict[str, str] | None = None

    def sign(self, signing_input: bytes) -> bytes:
        reply = self._client.sign(KeyId=self._key_id, Message=signing_input, MessageType="RAW",
                                  SigningAlgorithm="ECDSA_SHA_256")
        r, s = decode_dss_signature(reply["Signature"])  # KMS returns DER; JWS wants raw r || s
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    def public_jwk(self) -> dict[str, str]:
        if self._jwk is None:
            der = self._client.get_public_key(KeyId=self._key_id)["PublicKey"]
            key = load_der_public_key(der)
            if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
                raise ValueError("the facade signer key must be ECC_NIST_P256")
            numbers = key.public_numbers()
            self._jwk = {"kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig", "kid": self.kid,
                         "x": b64url(numbers.x.to_bytes(32, "big")), "y": b64url(numbers.y.to_bytes(32, "big"))}
        return dict(self._jwk)
