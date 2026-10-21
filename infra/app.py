"""HEARTHWARD CDK app: the deployable slice of blueprint §2 for gate G3.

    cd infra && uv run python app.py      # synthesizes to infra/cdk.out; makes no AWS calls

In this stack: the four DynamoDB tables, the three KMS keys, the Cognito user pool (Essentials, managed login) with
the MCP resource server and Alexa app client, the `auth` HTTP API in front of the `facade_as` Lambda, and the `mcp`
HTTP API in front of the Fargate task through a VPC link and an internal ALB. The ingest Lambdas, fusion, FalkorDB,
broker, web API and InterventionLadder arrive with the gates that need them.

Everything is configured from the `hearthward` context in `cdk.context.json`; the stack is account-agnostic, so
synthesis never looks anything up.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import aws_cdk as cdk
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as patterns
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

ROOT = Path(__file__).resolve().parent.parent
MCP_PORT = 8080
HEALTH_PATH = "/.well-known/oauth-protected-resource"
DYNAMODB_WRITES = ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem",
                   "dynamodb:TransactWriteItems"]
DEFAULTS: dict[str, Any] = {
    "synthetic": True, "environment": "dev", "region": "us-east-1", "cognito_domain_prefix": "hearthward-synthetic",
    "alexa_redirect_uris": [], "facade_service_clients": {}, "tags": {"project": "hearthward"}}


class HearthwardStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, settings: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.settings = settings
        for key, value in settings["tags"].items():
            cdk.Tags.of(self).add(key, str(value))

        self.attest_key = self._signing_key("AttestSigner", "attest-signer",
                                            "Signs HEARTHWARD attestations and the web presentation (TB-8)")
        self.facade_key = self._signing_key("FacadeTokenSigner", "facade-token-signer",
                                            "Signs the OAuth facade's ES256 service tokens")
        self.data_key = kms.Key(self, "DataKey", alias="alias/hearthward/data-key", enable_key_rotation=True,
                                description="Encrypts the HEARTHWARD DynamoDB tables",
                                removal_policy=cdk.RemovalPolicy.RETAIN)

        # Key attributes are `pk` and `sk` (strings) because ingest/ledger.py writes them: `pk` holds the household
        # and `sk` the zero-padded sequence (plus the HEAD, dedup and quarantine items that share the table).
        self.event_ledger = self._table("EventLedger", "HearthwardEventLedger", stream=True)
        self.security_state = self._table("SecurityState", "HearthwardSecurityState")
        self.proof_ledger = self._table("ProofLedger", "HearthwardProofLedger")
        self.nonces = self._table("Nonces", "HearthwardNonces", sort_key=False, ttl="expires_at")

        # The two API endpoints are known before anything that needs them, which breaks the URL cycles.
        self.auth_api = apigw.HttpApi(self, "AuthApi", api_name="hearthward-auth")
        self.mcp_api = apigw.HttpApi(self, "McpApi", api_name="hearthward-mcp")
        self.resource_uri = f"{self.mcp_api.api_endpoint}/mcp"
        self.facade_issuer = self.auth_api.api_endpoint

        self._identity()
        self._facade()
        self._mcp()
        self._outputs()

    # -- building blocks -----------------------------------------------------------------------------------

    def _signing_key(self, construct_id: str, alias: str, description: str) -> kms.Key:
        return kms.Key(self, construct_id, alias=f"alias/hearthward/{alias}", description=description,
                       key_spec=kms.KeySpec.ECC_NIST_P256, key_usage=kms.KeyUsage.SIGN_VERIFY,
                       removal_policy=cdk.RemovalPolicy.RETAIN)

    def _table(self, construct_id: str, name: str, *, stream: bool = False, sort_key: bool = True,
               ttl: str | None = None) -> dynamodb.Table:
        return dynamodb.Table(
            self, construct_id, table_name=name,
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING) if sort_key else None,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED, encryption_key=self.data_key,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True),
            stream=dynamodb.StreamViewType.NEW_IMAGE if stream else None,
            time_to_live_attribute=ttl, deletion_protection=True, removal_policy=cdk.RemovalPolicy.RETAIN)

    # -- identity: Cognito -------------------------------------------------------------------------------------

    def _identity(self) -> None:
        self.user_pool = cognito.UserPool(
            self, "UserPool", user_pool_name="hearthward", feature_plan=cognito.FeaturePlan.ESSENTIALS,
            self_sign_up_enabled=False, sign_in_aliases=cognito.SignInAliases(email=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY, deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.RETAIN)
        self.domain = self.user_pool.add_domain(
            "ManagedLogin", cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=self.settings["cognito_domain_prefix"]),
            managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN)
        # RFC 8707: the resource server's identifier is the canonical MCP URI, so Cognito binds it into `aud`.
        tools = cognito.ResourceServerScope(scope_name="tools", scope_description="Call HEARTHWARD tools")
        self.resource_server = self.user_pool.add_resource_server(
            "McpResource", identifier=self.resource_uri, user_pool_resource_server_name="hearthward-mcp",
            scopes=[tools])
        self.alexa_client = self.user_pool.add_client(
            "AlexaClient", user_pool_client_name="alexa-plus", generate_secret=True,
            prevent_user_existence_errors=True, enable_token_revocation=True,
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.resource_server(self.resource_server, tools)],
                callback_urls=self.settings["alexa_redirect_uris"]),
            access_token_validity=cdk.Duration.hours(1), refresh_token_validity=cdk.Duration.days(30))
        # OAuth only: the client may refresh tokens but offers no password, SRP or custom sign-in flow of its own.
        self.alexa_client.node.default_child.explicit_auth_flows = ["ALLOW_REFRESH_TOKEN_AUTH"]
        self.cognito_issuer = f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool.user_pool_id}"
        self.cognito_domain_url = self.domain.base_url()

    # -- the `auth` API: facade_as ---------------------------------------------------------------------------------

    def _facade(self) -> None:
        self.facade_fn = lambda_.DockerImageFunction(
            self, "FacadeAs", function_name="hearthward-facade-as", timeout=cdk.Duration.seconds(10),
            memory_size=512,
            log_group=logs.LogGroup(self, "FacadeLogs", retention=logs.RetentionDays.ONE_MONTH),
            code=lambda_.DockerImageCode.from_image_asset(str(ROOT), file="infra/Dockerfile.facade"),
            environment={
                "HW_FACADE_ISSUER": self.facade_issuer, "HW_MCP_RESOURCE_URI": self.resource_uri,
                "HW_COGNITO_DOMAIN": self.cognito_domain_url, "HW_FACADE_SIGNER_KEY_ID": self.facade_key.key_id,
                "HW_FACADE_SERVICE_CLIENTS": json.dumps(self.settings["facade_service_clients"], sort_keys=True)})
        self.facade_key.grant(self.facade_fn, "kms:Sign", "kms:GetPublicKey")
        integration = integrations.HttpLambdaIntegration("FacadeIntegration", self.facade_fn)
        for method, path in ((apigw.HttpMethod.GET, "/.well-known/oauth-authorization-server"),
                             (apigw.HttpMethod.GET, "/.well-known/openid-configuration"),
                             (apigw.HttpMethod.GET, "/jwks"), (apigw.HttpMethod.GET, "/authorize"),
                             (apigw.HttpMethod.POST, "/token")):
            self.auth_api.add_routes(path=path, methods=[method], integration=integration)

    # -- the `mcp` API: Fargate behind an internal ALB -----------------------------------------------------------

    def _mcp(self) -> None:
        self.vpc = ec2.Vpc(self, "Vpc", max_azs=2, nat_gateways=1)
        cluster = ecs.Cluster(self, "Cluster", vpc=self.vpc, container_insights_v2=ecs.ContainerInsights.ENABLED)
        self.mcp_service = patterns.ApplicationLoadBalancedFargateService(
            self, "McpService", cluster=cluster, cpu=512, memory_limit_mib=1024, desired_count=1,
            public_load_balancer=False, listener_port=80, protocol=elbv2.ApplicationProtocol.HTTP,
            task_image_options=patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_asset(str(ROOT), file="infra/Dockerfile.mcp"),
                container_port=MCP_PORT, container_name="mcp",
                log_driver=ecs.LogDrivers.aws_logs(stream_prefix="mcp", log_retention=logs.RetentionDays.ONE_MONTH),
                environment={
                    "HW_MCP_RESOURCE_URI": self.resource_uri, "HW_FACADE_ISSUER": self.facade_issuer,
                    "HW_COGNITO_ISSUER": self.cognito_issuer, "HW_COGNITO_DOMAIN": self.cognito_domain_url,
                    "HW_ALEXA_CLIENT_ID": self.alexa_client.user_pool_client_id,
                    "HW_LEDGER_TABLE": self.event_ledger.table_name}))
        service = self.mcp_service
        service.target_group.configure_health_check(path=HEALTH_PATH, healthy_http_codes="200")
        # API Gateway may forward the ALB's own name as Host; DNS-rebinding protection allows exactly that name.
        service.task_definition.default_container.add_environment(
            "HW_EXTRA_ALLOWED_HOSTS", service.load_balancer.load_balancer_dns_name)

        role = service.task_definition.task_role
        self.event_ledger.grant_read_data(role)       # S+ tools read the ledger; ingest Lambdas write it
        self.security_state.grant_read_data(role)
        # TB-5: an explicit deny wins over any allow, so the mcp role can never write SecurityState.
        role.add_to_principal_policy(iam.PolicyStatement(
            sid="DenySecurityStateWrites", effect=iam.Effect.DENY, actions=DYNAMODB_WRITES,
            resources=[self.security_state.table_arn, f"{self.security_state.table_arn}/index/*"]))

        link_sg = ec2.SecurityGroup(self, "VpcLinkSg", vpc=self.vpc, description="API Gateway VPC link")
        vpc_link = apigw.VpcLink(self, "McpVpcLink", vpc=self.vpc, security_groups=[link_sg],
                                 subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS))
        service.load_balancer.connections.allow_from(link_sg, ec2.Port.tcp(80), "API Gateway VPC link")
        integration = integrations.HttpAlbIntegration("McpIntegration", service.listener, vpc_link=vpc_link)
        self.mcp_api.add_routes(path="/mcp", methods=[apigw.HttpMethod.POST], integration=integration)
        self.mcp_api.add_routes(path=HEALTH_PATH, methods=[apigw.HttpMethod.GET], integration=integration)

    # -- outputs: the values G2 and G3 name as environment variables ---------------------------------------------

    def _outputs(self) -> None:
        """CloudFormation output ids cannot contain underscores, so each output's description carries the variable."""
        for name, value in {
            "HW_MCP_RESOURCE_URI": self.resource_uri, "HW_FACADE_ISSUER": self.facade_issuer,
            "HW_COGNITO_ISSUER": self.cognito_issuer, "HW_COGNITO_DOMAIN": self.cognito_domain_url,
            "HW_ALEXA_CLIENT_ID": self.alexa_client.user_pool_client_id,
            "AWS_REGION": self.region, "HW_LEDGER_TABLE": self.event_ledger.table_name,
            "HW_ATTEST_KEY_ARN": self.attest_key.key_arn,
        }.items():
            cdk.CfnOutput(self, "".join(part.capitalize() for part in name.lower().split("_")), value=value,
                          description=name)


def load_settings(app: cdk.App) -> dict[str, Any]:
    configured = app.node.try_get_context("hearthward") or {}
    return {**DEFAULTS, **configured, "tags": {**DEFAULTS["tags"], **configured.get("tags", {})}}


def build_app(context: dict[str, Any] | None = None) -> cdk.App:
    """The CDK CLI injects cdk.json and cdk.context.json; a direct `python app.py` run reads the latter itself."""
    if context is None:
        path = Path(__file__).resolve().parent / "cdk.context.json"
        context = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    app = cdk.App(context=context, outdir=None if os.environ.get("CDK_OUTDIR") else str(Path(__file__).resolve().parent / "cdk.out"))
    settings = load_settings(app)
    HearthwardStack(app, f"Hearthward-{settings['environment']}", settings,
                    env=cdk.Environment(region=settings["region"]))
    return app


if __name__ == "__main__":
    build_app().synth()
