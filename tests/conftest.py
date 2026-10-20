"""Shared fixtures: the blueprint spec, the orrery's reference runs and a moto-backed ledger (no real AWS)."""

from __future__ import annotations

from typing import Any

import boto3
import orrery
import pytest
from moto import mock_aws

from hearthward.ingest.ledger import Ledger
from tests.support import REGION, TABLE, create_table


@pytest.fixture(scope="session")
def spec() -> dict[str, Any]:
    return orrery.load_spec()


@pytest.fixture(scope="session")
def orrery_runs(spec: dict[str, Any]) -> dict[str, orrery.Result]:
    """All six scenarios at seed 7, exactly as `orrery --selftest` runs them."""
    problems, results = orrery.selftest(spec)
    assert problems == []
    return results


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch):
    """moto's in-process AWS. The credentials are moto's dummy values, not secrets, and nothing leaves the process."""
    for name, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                        "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(name, value)
    with mock_aws():
        yield


@pytest.fixture
def dynamodb(aws: None) -> Any:
    client = boto3.client("dynamodb", region_name=REGION)
    create_table(client)
    return client


@pytest.fixture
def ledger(dynamodb: Any) -> Ledger:
    """A ledger on an empty moto table, with retries that never sleep."""
    return Ledger(TABLE, REGION, client=dynamodb, sleep=lambda _: None)
