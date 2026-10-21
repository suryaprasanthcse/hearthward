"""Gate G5: the InterventionLadder state machine (blueprint §8) and the calls that drive it."""

from __future__ import annotations

import json
import re
from typing import Any

import boto3
import pipeline
import pytest
from botocore.stub import Stubber

from hearthward.ladder import signals

pytestmark = pytest.mark.gate_g5

ARN = "arn:aws:states:us-east-1:123456789012:stateMachine:InterventionLadder"
TASK_TOKEN = "AAAAKgAAAAIAAAAAAAAAAexample"
LAMBDA = "arn:aws:states:::lambda:invoke"
WAIT = "arn:aws:states:::lambda:invoke.waitForTaskToken"


@pytest.fixture(scope="module")
def asl() -> dict[str, Any]:
    return signals.definition()


def walk(states: dict[str, Any]):
    """Every state in a (possibly nested) States block, with the block that holds it."""
    for name, state in states.items():
        yield name, state, states
        if "ItemProcessor" in state:
            yield from walk(state["ItemProcessor"]["States"])
        for branch in state.get("Branches", []):
            yield from walk(branch["States"])


def targets(state: dict[str, Any]) -> list[str]:
    out = [state[k] for k in ("Next", "Default") if k in state]
    out += [c["Next"] for c in state.get("Choices", [])] + [c["Next"] for c in state.get("Catch", [])]
    return out


# ── structure ───────────────────────────────────────────────────────────────────────────────────────────


def test_the_definition_is_json_with_a_start_state_and_a_bounded_run(asl: dict[str, Any]) -> None:
    assert asl["StartAt"] == "L1Notify" and asl["StartAt"] in asl["States"]
    assert isinstance(asl["TimeoutSeconds"], int) and 0 < asl["TimeoutSeconds"] <= 86_400


def test_every_transition_points_at_a_state_in_the_same_block(asl: dict[str, Any]) -> None:
    blocks = [asl, *[s["ItemProcessor"] for _, s, _ in walk(asl["States"]) if "ItemProcessor" in s],
              *[b for _, s, _ in walk(asl["States"]) for b in s.get("Branches", [])]]
    assert len(blocks) == 4   # the top level, the Map processor and the two L1 branches
    for block in blocks:
        assert block["StartAt"] in block["States"]
        for name, state in block["States"].items():
            for target in targets(state):
                assert target in block["States"], f"{name} -> {target}"


def test_every_state_is_reachable_and_every_path_ends(asl: dict[str, Any]) -> None:
    blocks = [asl, *[s["ItemProcessor"] for _, s, _ in walk(asl["States"]) if "ItemProcessor" in s],
              *[b for _, s, _ in walk(asl["States"]) for b in s.get("Branches", [])]]
    for block in blocks:
        seen, todo = set(), [block["StartAt"]]
        while todo:
            name = todo.pop()
            if name not in seen:
                seen.add(name)
                todo += targets(block["States"][name])
        assert seen == set(block["States"])
        for state in block["States"].values():
            assert state.get("End") is True or state["Type"] in {"Succeed", "Fail", "Choice"} or "Next" in state


def test_only_known_state_types_and_no_real_account_ids(asl: dict[str, Any]) -> None:
    for name, state, _ in walk(asl["States"]):
        assert state["Type"] in {"Task", "Pass", "Choice", "Parallel", "Map", "Succeed", "Fail", "Wait"}, name
    text = json.dumps(asl)
    assert not re.search(r"\b\d{12}\b", text) and "arn:aws:lambda" not in text
    assert set(re.findall(r"\$\{(\w+)\}", text)) == {
        "NotifyCaregiverWebFunctionArn", "NotifyElderPhoneFunctionArn", "CheckEpisodeFunctionArn",
        "NotifyCircleMemberFunctionArn", "ConcludeLadderFunctionArn"}


# ── L1, L2, L3 ──────────────────────────────────────────────────────────────────────────────────────────


def test_l1_notifies_the_caregiver_web_view_and_the_elders_phone_in_parallel(asl: dict[str, Any]) -> None:
    l1 = asl["States"]["L1Notify"]
    assert l1["Type"] == "Parallel" and l1["Next"] == "L2EscalateCircle" and l1["ResultPath"] is None
    functions = [b["States"][b["StartAt"]]["Parameters"]["FunctionName"] for b in l1["Branches"]]
    assert functions == ["${NotifyCaregiverWebFunctionArn}", "${NotifyElderPhoneFunctionArn}"]
    for branch in l1["Branches"]:
        task = branch["States"][branch["StartAt"]]
        catch = task["Catch"][0]
        assert catch["ErrorEquals"] == ["States.ALL"] and branch["States"][catch["Next"]]["Type"] == "Pass"


def test_l2_is_a_sequential_map_over_the_circle_in_priority_order(asl: dict[str, Any]) -> None:
    l2 = asl["States"]["L2EscalateCircle"]
    assert l2["Type"] == "Map" and l2["ItemsPath"] == "$.circle" and l2["MaxConcurrency"] == 1
    assert l2["ItemProcessor"]["ProcessorConfig"]["Mode"] == "INLINE" and l2["Next"] == "ConcludeL2"
    assert l2["ItemSelector"]["member.$"] == "$$.Map.Item.Value"


def test_the_callback_task_waits_90_seconds_with_a_30_second_heartbeat(asl: dict[str, Any]) -> None:
    waits = [(n, s) for n, s, _ in walk(asl["States"]) if s["Type"] == "Task" and s["Resource"].endswith(".waitForTaskToken")]
    assert [n for n, _ in waits] == ["NotifyMember"]
    task = waits[0][1]
    assert task["Resource"] == WAIT and task["TimeoutSeconds"] == 90 and task["HeartbeatSeconds"] == 30
    assert task["HeartbeatSeconds"] < task["TimeoutSeconds"]
    assert task["Parameters"]["Payload"]["taskToken.$"] == "$$.Task.Token"


def test_a_timeout_or_lost_heartbeat_moves_on_and_other_errors_do_not_stop_the_ladder(asl: dict[str, Any]) -> None:
    task = asl["States"]["L2EscalateCircle"]["ItemProcessor"]["States"]["NotifyMember"]
    first, second = task["Catch"]
    assert first["ErrorEquals"] == ["States.Timeout", "States.HeartbeatTimeout"] and first["Next"] == "MemberTimedOut"
    assert second["ErrorEquals"] == ["States.ALL"] and second["Next"] == "MemberFailed"
    for retry in task["Retry"]:  # never retry the wait itself: a retried callback would resend the notification
        assert "States.Timeout" not in retry["ErrorEquals"] and "States.ALL" not in retry["ErrorEquals"]


def test_every_member_iteration_ends_with_a_named_outcome(asl: dict[str, Any]) -> None:
    states = asl["States"]["L2EscalateCircle"]["ItemProcessor"]["States"]
    outcomes = {n: s["Parameters"]["outcome"] for n, s in states.items() if n.startswith("Member")}
    assert outcomes == {"MemberAcknowledged": "ACK", "MemberEscalated": "ESCALATE", "MemberTimedOut": "TIMEOUT",
                        "MemberFailed": "FAILED", "MemberSkipped": "SKIPPED"}
    assert all(states[n]["End"] is True for n in outcomes)


def test_l3_is_deferred_and_the_ladder_always_ends(asl: dict[str, Any]) -> None:
    states = asl["States"]
    assert states["L3Deferred"]["Result"] == {"level": "L3", "status": "deferred"}
    assert states["L3Deferred"]["Next"] == "LadderExhausted"
    assert states["LadderResolved"]["Type"] == states["LadderExhausted"]["Type"] == "Succeed"
    assert states["ConcludeL2"]["Catch"][0]["Next"] == "L3Deferred"  # if we cannot tell, keep escalating, not resolve


# ── routing, evaluated ──────────────────────────────────────────────────────────────────────────────────


def lookup(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.removeprefix("$.").split("."):
        value = value[part]
    return value


def route(state: dict[str, Any], data: dict[str, Any]) -> str:
    """The Choice state's rules for the operators this ladder uses."""
    for choice in state["Choices"]:
        try:
            value = lookup(data, choice["Variable"])
        except KeyError:
            continue
        if "StringEquals" in choice and value == choice["StringEquals"]:
            return choice["Next"]
        if "BooleanEquals" in choice and value is choice["BooleanEquals"]:
            return choice["Next"]
    return state["Default"]


def test_the_signal_choice_routes_ack_and_escalate_and_escalates_anything_else(asl: dict[str, Any]) -> None:
    choice = asl["States"]["L2EscalateCircle"]["ItemProcessor"]["States"]["RouteSignal"]
    assert route(choice, {"reply": {"signal": "ACK"}}) == "MemberAcknowledged"
    assert route(choice, {"reply": {"signal": "ESCALATE"}}) == "MemberEscalated"
    for odd in ({"reply": {"signal": "ack"}}, {"reply": {"signal": ""}}, {"reply": {}}, {"reply": {"signal": 7}}):
        assert route(choice, odd) == "MemberEscalated"   # fail toward more protection


def test_an_acknowledged_episode_makes_the_remaining_members_skip(asl: dict[str, Any]) -> None:
    choice = asl["States"]["L2EscalateCircle"]["ItemProcessor"]["States"]["StillOpen"]
    assert route(choice, {"check": {"open": False}}) == "MemberSkipped"
    assert route(choice, {"check": {"open": True}}) == "NotifyMember"
    assert route(choice, {"error": "x"}) == "NotifyMember"   # an unreadable check does not silence the ladder


def test_the_resolution_choice(asl: dict[str, Any]) -> None:
    choice = asl["States"]["WasResolved"]
    assert route(choice, {"conclusion": {"resolved": True}}) == "LadderResolved"
    assert route(choice, {"conclusion": {"resolved": False}}) == "L3Deferred"


# ── the pipeline's own check ─────────────────────────────────────────────────────────────────────────────


def test_the_pipeline_ladder_check_passes() -> None:
    outcome = pipeline.g5_ladder(pipeline.Context(probe=False))
    assert outcome.status == pipeline.PASS and "1 callback tasks" in outcome.detail


@pytest.mark.parametrize("mutate, word", [
    (lambda d: d["States"]["L2EscalateCircle"]["ItemProcessor"]["States"]["NotifyMember"].pop("TimeoutSeconds"),
     "TimeoutSeconds"),
    (lambda d: d["States"]["L2EscalateCircle"]["ItemProcessor"]["States"]["NotifyMember"].pop("HeartbeatSeconds"),
     "HeartbeatSeconds"),
    (lambda d: d["States"]["L2EscalateCircle"]["ItemProcessor"]["States"]["NotifyMember"].__setitem__(
        "Resource", LAMBDA), "no .waitForTaskToken"),
    (lambda d: d.__setitem__("StartAt", "Nowhere"), "StartAt"),
])
def test_the_pipeline_ladder_check_fails_on_a_weakened_definition(
        tmp_path: Any, monkeypatch: pytest.MonkeyPatch, asl: dict[str, Any], mutate: Any, word: str) -> None:
    broken = json.loads(json.dumps(asl))
    mutate(broken)
    target = tmp_path / "src/hearthward/ladder"
    target.mkdir(parents=True)
    (target / "intervention_ladder.asl.json").write_text(json.dumps(broken), encoding="utf-8")
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    outcome = pipeline.g5_ladder(pipeline.Context(probe=False))
    assert outcome.status == pipeline.FAIL and word in outcome.detail


# ── signals ─────────────────────────────────────────────────────────────────────────────────────────────


def sfn() -> tuple[Any, Stubber]:
    client = boto3.client("stepfunctions", region_name="us-east-1", aws_access_key_id="testing",
                          aws_secret_access_key="testing")
    return client, Stubber(client)


def test_the_ladder_starts_once_per_episode_named_after_it() -> None:
    client, stub = sfn()
    circle = [{"id": "m_robin", "priority": 1}, {"id": "m_sam", "priority": 2}]
    payload = json.dumps({"episode": "ep_01HZX3K9M2N4P6Q8R0S2T4V6W8", "household": "hh_synthetic", "circle": circle},
                         sort_keys=True, separators=(",", ":"))
    stub.add_response("start_execution", {"executionArn": ARN + ":ep", "startDate": __import__("datetime").datetime(
        2030, 1, 1)}, {"stateMachineArn": ARN, "name": "ep_01HZX3K9M2N4P6Q8R0S2T4V6W8", "input": payload})
    with stub:
        arn = signals.start_ladder(client, ARN, episode_id="ep_01HZX3K9M2N4P6Q8R0S2T4V6W8", household="hh_synthetic",
                                   circle=circle)
    assert arn == ARN + ":ep"


def test_a_second_ladder_for_the_same_episode_is_refused_quietly() -> None:
    client, stub = sfn()
    stub.add_client_error("start_execution", service_error_code="ExecutionAlreadyExists")
    with stub:
        assert signals.start_ladder(client, ARN, episode_id="ep_x", household="h", circle=[]) is None


def test_other_start_errors_surface() -> None:
    client, stub = sfn()
    stub.add_client_error("start_execution", service_error_code="StateMachineDoesNotExist")
    with stub, pytest.raises(Exception, match="StateMachineDoesNotExist"):
        signals.start_ladder(client, ARN, episode_id="ep_x", household="h", circle=[])


@pytest.mark.parametrize("call, signal", [(signals.escalate, "ESCALATE"), (signals.acknowledge, "ACK")])
def test_signals_are_send_task_success_with_the_signal_object(call: Any, signal: str) -> None:
    client, stub = sfn()
    stub.add_response("send_task_success", {}, {"taskToken": TASK_TOKEN, "output": json.dumps({"signal": signal},
                                                                                              separators=(",", ":"))})
    with stub:
        call(client, TASK_TOKEN)
        stub.assert_no_pending_responses()


def test_the_escalate_output_is_exactly_the_documented_object() -> None:
    client, stub = sfn()
    sent: list[dict[str, Any]] = []
    client.meta.events.register("before-parameter-build.stepfunctions.SendTaskSuccess",
                                lambda params, **_: sent.append(dict(params)))
    stub.add_response("send_task_success", {})
    with stub:
        signals.escalate(client, TASK_TOKEN)
    assert json.loads(sent[0]["output"]) == {"signal": "ESCALATE"} and sent[0]["taskToken"] == TASK_TOKEN


def test_unknown_signals_are_refused_before_any_call() -> None:
    client, _ = sfn()
    for bad in ("", "ack", "DISMISS", "ESCALATE; DROP"):
        with pytest.raises(ValueError):
            signals.send_signal(client, TASK_TOKEN, bad)


def test_a_heartbeat_keeps_the_task_alive() -> None:
    client, stub = sfn()
    stub.add_response("send_task_heartbeat", {}, {"taskToken": TASK_TOKEN})
    with stub:
        signals.heartbeat(client, TASK_TOKEN)
        stub.assert_no_pending_responses()
