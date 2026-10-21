"""Calls into the ladder: start it for an episode, and signal a waiting step through its task token.

`client` is a boto3 Step Functions client. Nothing here is called by the MCP tools yet; the fusion worker owns the
task token it stores on the episode and is the one that heartbeats and escalates.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

DEFINITION = Path(__file__).resolve().parent / "intervention_ladder.asl.json"
SIGNALS = ("ACK", "ESCALATE")


def definition() -> dict[str, Any]:
    return json.loads(DEFINITION.read_bytes())


def start_ladder(client: Any, state_machine_arn: str, *, episode_id: str, household: str,
                 circle: Sequence[Mapping[str, Any]]) -> str | None:
    """Start the one ladder for this episode (the execution is named after it). Returns the execution ARN, or None
    when a ladder with different input already runs for the episode."""
    payload = {"episode": episode_id, "household": household, "circle": [dict(m) for m in circle]}
    try:
        reply = client.start_execution(stateMachineArn=state_machine_arn, name=episode_id,
                                       input=json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ExecutionAlreadyExists":
            return None
        raise
    return reply["executionArn"]


def send_signal(client: Any, task_token: str, signal: str) -> None:
    """SendTaskSuccess(token, {"signal": signal}); the Choice state after the callback task routes it."""
    if signal not in SIGNALS:
        raise ValueError(f"unknown ladder signal: {signal!r}")
    client.send_task_success(taskToken=task_token, output=json.dumps({"signal": signal}, separators=(",", ":")))


def escalate(client: Any, task_token: str) -> None:
    """New evidence arrived: move the ladder on to the next member at once."""
    send_signal(client, task_token, "ESCALATE")


def acknowledge(client: Any, task_token: str) -> None:
    send_signal(client, task_token, "ACK")


def heartbeat(client: Any, task_token: str) -> None:
    """Keeps a callback task alive inside its 30 s heartbeat window."""
    client.send_task_heartbeat(taskToken=task_token)
