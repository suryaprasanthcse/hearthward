"""Gate G2 settings, read from the environment (the five variables `tools/pipeline.py` names for G2)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
SERVICE_SCOPE = "mcp:service"
LEEWAY_SECONDS = 60


@dataclass(frozen=True)
class McpConfig:
    resource_uri: str
    facade_issuer: str
    cognito_issuer: str
    cognito_domain: str
    alexa_client_id: str
    extra_hosts: tuple[str, ...] = ()  # e.g. the internal ALB's DNS name, which API Gateway may forward as Host

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> McpConfig:
        env = os.environ if env is None else env
        names = {"resource_uri": "HW_MCP_RESOURCE_URI", "facade_issuer": "HW_FACADE_ISSUER",
                 "cognito_issuer": "HW_COGNITO_ISSUER", "cognito_domain": "HW_COGNITO_DOMAIN",
                 "alexa_client_id": "HW_ALEXA_CLIENT_ID"}
        missing = [var for var in names.values() if not env.get(var)]
        if missing:
            raise RuntimeError("not set: " + ", ".join(missing))
        extra = tuple(h.strip() for h in env.get("HW_EXTRA_ALLOWED_HOSTS", "").split(",") if h.strip())
        return cls(**{field: env[var] for field, var in names.items()}, extra_hosts=extra)

    @property
    def host(self) -> str:
        return urlsplit(self.resource_uri).netloc

    @property
    def allowed_hosts(self) -> list[str]:
        """P-6: the API Gateway host and `localhost:*`, never the SDK's localhost-only default."""
        return [self.host, *self.extra_hosts, "localhost:*", "127.0.0.1:*"]

    @property
    def allowed_origins(self) -> list[str]:
        return [f"https://{self.host}", "http://localhost:*", "http://127.0.0.1:*"]

    @property
    def user_scope(self) -> str:
        return f"{self.resource_uri}/tools"

    @property
    def scopes_supported(self) -> list[str]:
        return ["openid", self.user_scope, SERVICE_SCOPE]

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    @property
    def facade_jwks_url(self) -> str:
        return f"{self.facade_issuer}/jwks"
