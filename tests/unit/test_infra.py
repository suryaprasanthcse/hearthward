"""Gate G3: the synthesized CloudFormation for the CDK app. Synthesis is account-agnostic and calls no AWS API."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from aws_cdk import assertions

pytestmark = pytest.mark.gate_g3

INFRA = Path(__file__).resolve().parents[2] / "infra"
WRITES = {"dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
          "dynamodb:TransactWriteItems"}
TABLES = {"EventLedger": "HearthwardEventLedger", "SecurityState": "HearthwardSecurityState",
          "ProofLedger": "HearthwardProofLedger", "Nonces": "HearthwardNonces"}


@pytest.fixture(scope="module")
def infra_app(tmp_path_factory: pytest.TempPathFactory) -> Any:
    spec = importlib.util.spec_from_file_location("hearthward_infra_app", INFRA / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mp = pytest.MonkeyPatch()
    mp.setenv("CDK_OUTDIR", str(tmp_path_factory.mktemp("cdk-out")))
    yield module
    mp.undo()


@pytest.fixture(scope="module")
def stack(infra_app: Any) -> Any:
    app = infra_app.build_app()
    return app.node.find_child("Hearthward-dev")


@pytest.fixture(scope="module")
def template(stack: Any) -> assertions.Template:
    return assertions.Template.from_stack(stack)


@pytest.fixture(scope="module")
def cfn(template: assertions.Template) -> dict[str, Any]:
    return template.to_json()


def resources(cfn: dict[str, Any], kind: str) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in cfn["Resources"].items() if v["Type"] == kind}


def logical_id(cfn: dict[str, Any], kind: str, **props: Any) -> str:
    (match,) = [k for k, v in resources(cfn, kind).items()
                if all(v["Properties"].get(p) == want for p, want in props.items())]
    return match


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


# ── the app itself ───────────────────────────────────────────────────────────────────────────────────────


def test_cdk_files_exist_and_cdk_json_runs_app_py_through_uv() -> None:
    for name in ("app.py", "cdk.json", "cdk.context.json"):
        assert (INFRA / name).is_file()
    assert json.loads((INFRA / "cdk.json").read_text(encoding="utf-8"))["app"] == "uv run python app.py"


def test_context_is_marked_synthetic_and_region_is_set() -> None:
    context = json.loads((INFRA / "cdk.context.json").read_text(encoding="utf-8"))["hearthward"]
    assert context["synthetic"] is True and context["region"] == "us-east-1"
    assert context["tags"]["data-origin"] == "synthetic"


def test_the_stack_is_account_agnostic_so_synthesis_needs_no_lookup(stack: Any) -> None:
    assert "123456789012" not in json.dumps(assertions.Template.from_stack(stack).to_json())


def test_every_resource_carries_the_synthetic_tags(cfn: dict[str, Any]) -> None:
    for kind in ("AWS::DynamoDB::Table", "AWS::KMS::Key", "AWS::Lambda::Function"):
        for resource in resources(cfn, kind).values():
            tags = {t["Key"]: t["Value"] for t in resource["Properties"]["Tags"]}
            assert tags["project"] == "hearthward" and tags["data-origin"] == "synthetic"


# ── DynamoDB ────────────────────────────────────────────────────────────────────────────────────────────


def test_four_tables_with_the_blueprint_names(cfn: dict[str, Any]) -> None:
    names = sorted(r["Properties"]["TableName"] for r in resources(cfn, "AWS::DynamoDB::Table").values())
    assert names == sorted(TABLES.values())


@pytest.mark.parametrize("name", ["HearthwardEventLedger", "HearthwardSecurityState", "HearthwardProofLedger"])
def test_hash_and_range_keys_match_what_the_ledger_code_writes(cfn: dict[str, Any], name: str) -> None:
    """`ingest/ledger.py` writes string attributes `pk` (household) and `sk` (sequence and sibling items)."""
    table = next(r for r in resources(cfn, "AWS::DynamoDB::Table").values() if r["Properties"]["TableName"] == name)
    props = table["Properties"]
    assert props["KeySchema"] == [{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}]
    assert {(a["AttributeName"], a["AttributeType"]) for a in props["AttributeDefinitions"]} == {("pk", "S"), ("sk", "S")}


def test_nonces_has_only_a_hash_key_and_a_ttl(cfn: dict[str, Any]) -> None:
    props = next(r for r in resources(cfn, "AWS::DynamoDB::Table").values()
                 if r["Properties"]["TableName"] == "HearthwardNonces")["Properties"]
    assert props["KeySchema"] == [{"AttributeName": "pk", "KeyType": "HASH"}]
    assert props["TimeToLiveSpecification"] == {"AttributeName": "expires_at", "Enabled": True}


def test_the_ledger_matches_the_g1_test_table_schema() -> None:
    from tests.support import create_table  # noqa: F401  (the G1 tests build the ledger table the same way)

    source = (Path(__file__).resolve().parents[1] / "support.py").read_text(encoding="utf-8")
    assert '"AttributeName": "pk", "KeyType": "HASH"' in source and '"AttributeName": "sk", "KeyType": "RANGE"' in source


def test_only_the_event_ledger_streams_and_it_streams_new_images(cfn: dict[str, Any]) -> None:
    for resource in resources(cfn, "AWS::DynamoDB::Table").values():
        props = resource["Properties"]
        if props["TableName"] == "HearthwardEventLedger":
            assert props["StreamSpecification"] == {"StreamViewType": "NEW_IMAGE"}
        else:
            assert "StreamSpecification" not in props


def test_tables_are_protected_encrypted_with_the_data_key_and_recoverable(cfn: dict[str, Any]) -> None:
    data_key = next(k for k, v in resources(cfn, "AWS::KMS::Key").items()
                    if v["Properties"].get("KeySpec", "SYMMETRIC_DEFAULT") == "SYMMETRIC_DEFAULT")
    for resource in resources(cfn, "AWS::DynamoDB::Table").values():
        props = resource["Properties"]
        assert props["DeletionProtectionEnabled"] is True
        assert props["PointInTimeRecoverySpecification"] == {"PointInTimeRecoveryEnabled": True}
        assert props["BillingMode"] == "PAY_PER_REQUEST"
        assert props["SSESpecification"]["SSEEnabled"] is True and props["SSESpecification"]["SSEType"] == "KMS"
        assert props["SSESpecification"]["KMSMasterKeyId"] == {"Fn::GetAtt": [data_key, "Arn"]}
        assert resource["DeletionPolicy"] == "Retain"


# ── KMS ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_three_keys_two_p256_signers_and_a_rotating_data_key(cfn: dict[str, Any]) -> None:
    keys = [v["Properties"] for v in resources(cfn, "AWS::KMS::Key").values()]
    assert len(keys) == 3
    signers = [k for k in keys if k.get("KeySpec") == "ECC_NIST_P256"]
    assert len(signers) == 2 and all(k["KeyUsage"] == "SIGN_VERIFY" for k in signers)
    assert all("EnableKeyRotation" not in k for k in signers)  # KMS cannot rotate asymmetric keys
    (data,) = [k for k in keys if k.get("KeySpec", "SYMMETRIC_DEFAULT") == "SYMMETRIC_DEFAULT"]
    assert data["EnableKeyRotation"] is True


def test_key_aliases(cfn: dict[str, Any]) -> None:
    aliases = {r["Properties"]["AliasName"] for r in resources(cfn, "AWS::KMS::Alias").values()}
    assert aliases == {"alias/hearthward/attest-signer", "alias/hearthward/facade-token-signer",
                       "alias/hearthward/data-key"}


# ── Cognito ─────────────────────────────────────────────────────────────────────────────────────────────


def test_user_pool_is_essentials_with_managed_login(cfn: dict[str, Any]) -> None:
    (pool,) = resources(cfn, "AWS::Cognito::UserPool").values()
    assert pool["Properties"]["UserPoolTier"] == "ESSENTIALS"
    assert pool["Properties"]["DeletionProtection"] == "ACTIVE"
    (domain,) = resources(cfn, "AWS::Cognito::UserPoolDomain").values()
    assert domain["Properties"]["ManagedLoginVersion"] == 2
    assert domain["Properties"]["Domain"] == "hearthward-synthetic"


def test_resource_server_identifier_is_the_canonical_mcp_uri(cfn: dict[str, Any]) -> None:
    """RFC 8707: Cognito binds the identifier into `aud`, which `mcp/auth.py` requires to equal the MCP URI."""
    (server,) = resources(cfn, "AWS::Cognito::UserPoolResourceServer").values()
    mcp_api = logical_id(cfn, "AWS::ApiGatewayV2::Api", Name="hearthward-mcp")
    assert server["Properties"]["Identifier"] == {"Fn::Join": ["", [{"Fn::GetAtt": [mcp_api, "ApiEndpoint"]}, "/mcp"]]}
    assert [s["ScopeName"] for s in server["Properties"]["Scopes"]] == ["tools"]
    assert outputs_by_variable(cfn)["HW_MCP_RESOURCE_URI"] == server["Properties"]["Identifier"]


def test_app_client_uses_only_the_authorization_code_flow(cfn: dict[str, Any]) -> None:
    (client,) = resources(cfn, "AWS::Cognito::UserPoolClient").values()
    props = client["Properties"]
    assert props["AllowedOAuthFlows"] == ["code"] and props["AllowedOAuthFlowsUserPoolClient"] is True
    assert props["GenerateSecret"] is True and props["PreventUserExistenceErrors"] == "ENABLED"
    assert props["EnableTokenRevocation"] is True and props["SupportedIdentityProviders"] == ["COGNITO"]
    assert "openid" in props["AllowedOAuthScopes"] and len(props["AllowedOAuthScopes"]) == 2
    assert props["ExplicitAuthFlows"] == ["ALLOW_REFRESH_TOKEN_AUTH"]  # no password, SRP or custom flow
    assert props["CallbackURLs"] and all(url.startswith("https://") for url in props["CallbackURLs"])
    assert props["AccessTokenValidity"] == 60 and props["RefreshTokenValidity"] == 43200


# ── API Gateway and compute ───────────────────────────────────────────────────────────────────────────────


def routes_of(cfn: dict[str, Any], api_name: str) -> set[str]:
    api = logical_id(cfn, "AWS::ApiGatewayV2::Api", Name=api_name)
    return {r["Properties"]["RouteKey"] for r in resources(cfn, "AWS::ApiGatewayV2::Route").values()
            if r["Properties"]["ApiId"] == {"Ref": api}}


def test_two_http_apis(cfn: dict[str, Any]) -> None:
    apis = resources(cfn, "AWS::ApiGatewayV2::Api").values()
    assert sorted(a["Properties"]["Name"] for a in apis) == ["hearthward-auth", "hearthward-mcp"]
    assert all(a["Properties"]["ProtocolType"] == "HTTP" for a in apis)


def test_auth_api_serves_exactly_the_facade_routes(cfn: dict[str, Any]) -> None:
    assert routes_of(cfn, "hearthward-auth") == {
        "GET /.well-known/oauth-authorization-server", "GET /.well-known/openid-configuration", "GET /jwks",
        "GET /authorize", "POST /token"}


def test_mcp_api_serves_exactly_mcp_and_the_protected_resource_metadata(cfn: dict[str, Any]) -> None:
    routes = routes_of(cfn, "hearthward-mcp")
    assert routes == {"POST /mcp", "GET /.well-known/oauth-protected-resource"}
    assert not any(r.endswith("/") and r != "/" for r in routes)  # P-1: no trailing-slash variant


def test_mcp_traffic_reaches_the_task_over_a_vpc_link_to_an_internal_alb(cfn: dict[str, Any]) -> None:
    (alb,) = resources(cfn, "AWS::ElasticLoadBalancingV2::LoadBalancer").values()
    assert alb["Properties"]["Scheme"] == "internal"
    (link,) = resources(cfn, "AWS::ApiGatewayV2::VpcLink").values()
    assert link["Properties"]["SubnetIds"]
    integrations = [r["Properties"] for r in resources(cfn, "AWS::ApiGatewayV2::Integration").values()
                    if r["Properties"].get("ConnectionType") == "VPC_LINK"]
    assert integrations and all(i["IntegrationType"] == "HTTP_PROXY" for i in integrations)


def test_target_health_check_uses_the_public_metadata_document(cfn: dict[str, Any]) -> None:
    (group,) = resources(cfn, "AWS::ElasticLoadBalancingV2::TargetGroup").values()
    assert group["Properties"]["HealthCheckPath"] == "/.well-known/oauth-protected-resource"
    (service,) = resources(cfn, "AWS::ECS::Service").values()
    assert [lb["ContainerPort"] for lb in service["Properties"]["LoadBalancers"]] == [8080]


def test_the_facade_is_a_container_lambda_with_only_the_kms_sign_permissions(cfn: dict[str, Any]) -> None:
    (fn,) = [r for r in resources(cfn, "AWS::Lambda::Function").values()
             if r["Properties"].get("FunctionName") == "hearthward-facade-as"]
    assert fn["Properties"]["PackageType"] == "Image"
    env = fn["Properties"]["Environment"]["Variables"]
    assert {"HW_FACADE_ISSUER", "HW_MCP_RESOURCE_URI", "HW_COGNITO_DOMAIN", "HW_FACADE_SIGNER_KEY_ID",
            "HW_FACADE_SERVICE_CLIENTS"} <= set(env)
    assert json.loads(env["HW_FACADE_SERVICE_CLIENTS"]) == {}  # digests only, and none in the synthetic context
    kms_actions = {a for p in resources(cfn, "AWS::IAM::Policy").values()
                   for s in p["Properties"]["PolicyDocument"]["Statement"]
                   if "Sid" not in s and any(str(a).startswith("kms:Sign") for a in as_list(s["Action"]))
                   for a in as_list(s["Action"])}
    assert kms_actions == {"kms:Sign", "kms:GetPublicKey"}


def test_mcp_container_gets_the_five_g2_variables_and_the_alb_host(cfn: dict[str, Any]) -> None:
    (task,) = resources(cfn, "AWS::ECS::TaskDefinition").values()
    (container,) = task["Properties"]["ContainerDefinitions"]
    env = {e["Name"] for e in container["Environment"]}
    assert {"HW_MCP_RESOURCE_URI", "HW_FACADE_ISSUER", "HW_COGNITO_ISSUER", "HW_COGNITO_DOMAIN", "HW_ALEXA_CLIENT_ID",
            "HW_EXTRA_ALLOWED_HOSTS"} <= env
    assert container["PortMappings"][0]["ContainerPort"] == 8080


def outputs_by_variable(cfn: dict[str, Any]) -> dict[str, Any]:
    return {o["Description"]: o["Value"] for o in cfn["Outputs"].values() if "Description" in o}


def test_outputs_name_the_gate_variables(cfn: dict[str, Any]) -> None:
    assert set(outputs_by_variable(cfn)) == {"HW_MCP_RESOURCE_URI", "HW_FACADE_ISSUER", "HW_COGNITO_ISSUER", "HW_COGNITO_DOMAIN",
                                   "HW_ALEXA_CLIENT_ID", "AWS_REGION", "HW_LEDGER_TABLE", "HW_ATTEST_KEY_ARN"}


# ── IAM least privilege (TB-5) ────────────────────────────────────────────────────────────────────────────


def mcp_role_policies(cfn: dict[str, Any]) -> list[dict[str, Any]]:
    (task,) = resources(cfn, "AWS::ECS::TaskDefinition").values()
    role_ref = task["Properties"]["TaskRoleArn"]["Fn::GetAtt"][0]
    return [p["Properties"]["PolicyDocument"] for p in resources(cfn, "AWS::IAM::Policy").values()
            if {"Ref": role_ref} in p["Properties"]["Roles"]]


def touches(statement: dict[str, Any], table_id: str) -> bool:
    return table_id in json.dumps(statement["Resource"])


def test_the_mcp_role_is_explicitly_denied_every_write_to_security_state(cfn: dict[str, Any]) -> None:
    table_id = logical_id(cfn, "AWS::DynamoDB::Table", TableName="HearthwardSecurityState")
    statements = [s for doc in mcp_role_policies(cfn) for s in doc["Statement"]]
    denies = [s for s in statements if s["Effect"] == "Deny" and touches(s, table_id)]
    assert len(denies) == 1 and set(as_list(denies[0]["Action"])) == WRITES
    assert any("/index/*" in json.dumps(r) for r in as_list(denies[0]["Resource"]))


def test_no_allow_statement_gives_the_mcp_role_a_write_to_security_state(cfn: dict[str, Any]) -> None:
    table_id = logical_id(cfn, "AWS::DynamoDB::Table", TableName="HearthwardSecurityState")
    for doc in mcp_role_policies(cfn):
        for statement in doc["Statement"]:
            if statement["Effect"] == "Allow" and touches(statement, table_id):
                actions = set(as_list(statement["Action"]))
                assert not (actions & WRITES) and "dynamodb:*" not in actions and "*" not in actions


def test_the_mcp_role_can_read_security_state_and_the_ledger(cfn: dict[str, Any]) -> None:
    allowed = {a for doc in mcp_role_policies(cfn) for s in doc["Statement"] if s["Effect"] == "Allow"
               for a in as_list(s["Action"])}
    assert {"dynamodb:GetItem", "dynamodb:Query"} <= allowed and not (allowed & WRITES)


def test_the_mcp_role_has_no_access_to_the_signing_keys_or_other_tables(cfn: dict[str, Any]) -> None:
    ids = {logical_id(cfn, "AWS::DynamoDB::Table", TableName=TABLES[n]) for n in ("ProofLedger", "Nonces")}
    ids |= {k for k, v in resources(cfn, "AWS::KMS::Key").items() if v["Properties"].get("KeySpec") == "ECC_NIST_P256"}
    text = json.dumps(mcp_role_policies(cfn))
    assert not any(f'"{i}"' in text for i in ids)


def test_no_allow_statement_in_the_stack_grants_every_action(cfn: dict[str, Any]) -> None:
    for policy in resources(cfn, "AWS::IAM::Policy").values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            if statement["Effect"] == "Allow":
                assert "*" not in as_list(statement["Action"])
                assert not any(str(a).endswith(":*") for a in as_list(statement["Action"]))
