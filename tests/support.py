"""Test helpers shared across suites: paths, the moto table layout and envelope utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
REGION = "us-east-1"
TABLE = "hw-test-ledger"


def strip_chain(envelope: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in envelope.items() if k not in ("seq", "prev", "head")}


def read_json(path: Path) -> Any:
    return json.loads(path.read_bytes())


def create_table(client: Any, name: str = TABLE) -> None:
    client.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                              {"AttributeName": "sk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
