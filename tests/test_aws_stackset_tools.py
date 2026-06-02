"""Unit and MCP-level tests for the stateless AWS setup-step tools and wrappers."""

from fastmcp import Client
import json
import pytest

from src.wrappers import (
    filter_org_units_response,
    filter_stackset_status_response,
    filter_member_stackset_check,
    parse_cft_quick_create_params,
)
from src.tools.aws_cloud_tools import (
    get_access_role_setup_steps,
    get_member_discovery_setup_steps,
    _STACKSET_NAME,
    _DEFAULT_TARGET_OU_ID,
    _ACCESS_STACK_NAME,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_response_data(result):
    if hasattr(result, "content"):
        content_list = result.content
    else:
        content_list = result
    if not isinstance(content_list, list) or len(content_list) == 0:
        raise AssertionError("Expected list response with at least one item")
    if not hasattr(content_list[0], "text"):
        raise AssertionError("Response item missing 'text' attribute")
    try:
        return json.loads(content_list[0].text)
    except json.JSONDecodeError:
        return content_list[0].text


# ---------------------------------------------------------------------------
# Wrapper unit tests — parse_cft_quick_create_params
# ---------------------------------------------------------------------------

class TestParseCftQuickCreateParams:
    _BASE = (
        "https://us-east-1.console.aws.amazon.com/cloudformation/home"
        "?region=us-east-1"
        "#/stacks/create/review"
        "?templateURL=https%3A%2F%2Fs3.amazonaws.com%2Fbucket%2Ftemplate.yaml"
        "&stackName=CommvaultPermissionsStack"
        "&param_HostedInfraRoleArn=arn%3Aaws%3Aiam%3A%3A123456789012%3Arole%2FMetallic"
        "&param_ExternalId=ext-abc-123"
    )

    def test_extracts_template_url(self):
        result = parse_cft_quick_create_params(self._BASE)
        assert result["templateUrl"] == "https://s3.amazonaws.com/bucket/template.yaml"

    def test_extracts_stack_name(self):
        result = parse_cft_quick_create_params(self._BASE)
        assert result["stackName"] == "CommvaultPermissionsStack"

    def test_extracts_params(self):
        result = parse_cft_quick_create_params(self._BASE)
        param_keys = {p["ParameterKey"] for p in result["params"]}
        assert "HostedInfraRoleArn" in param_keys
        assert "ExternalId" in param_keys

    def test_param_values_decoded(self):
        result = parse_cft_quick_create_params(self._BASE)
        role_param = next(p for p in result["params"] if p["ParameterKey"] == "HostedInfraRoleArn")
        assert role_param["ParameterValue"] == "arn:aws:iam::123456789012:role/Metallic"

    def test_no_params_in_url(self):
        url = (
            "https://console.aws.amazon.com/cloudformation/home?region=us-east-1"
            "#/stacks/create/review?templateURL=https%3A%2F%2Fs3.amazonaws.com%2Ft.yaml"
            "&stackName=MyStack"
        )
        result = parse_cft_quick_create_params(url)
        assert result["templateUrl"] == "https://s3.amazonaws.com/t.yaml"
        assert result["params"] == []

    def test_malformed_url_returns_defaults(self):
        result = parse_cft_quick_create_params("not-a-valid-url")
        assert result["stackName"] == "CommvaultPermissionsStack"
        assert isinstance(result["params"], list)


# ---------------------------------------------------------------------------
# Wrapper unit tests — filter_org_units_response
# ---------------------------------------------------------------------------

class TestFilterOrgUnitsResponse:
    def test_empty(self):
        result = filter_org_units_response({"OrganizationalUnits": []})
        assert result["totalOUs"] == 0
        assert result["ous"] == []

    def test_single_ou(self):
        result = filter_org_units_response({
            "OrganizationalUnits": [{"Id": "ou-abc-123", "Name": "Production"}]
        })
        assert result["totalOUs"] == 1
        assert result["ous"][0] == {"ouId": "ou-abc-123", "ouName": "Production"}

    def test_multiple_ous(self):
        ous = [
            {"Id": "ou-abc-001", "Name": "Dev"},
            {"Id": "ou-abc-002", "Name": "Prod"},
        ]
        result = filter_org_units_response({"OrganizationalUnits": ous})
        assert result["totalOUs"] == 2
        assert len(result["ous"]) == 2

    def test_missing_fields_handled(self):
        result = filter_org_units_response({"OrganizationalUnits": [{}]})
        assert result["ous"][0] == {"ouId": None, "ouName": None}


# ---------------------------------------------------------------------------
# Wrapper unit tests — filter_stackset_status_response
# ---------------------------------------------------------------------------

class TestFilterStacksetStatusResponse:
    def _make_summaries(self, statuses):
        return [
            {"StackInstanceStatus": {"DetailedStatus": s}, "Account": f"acct-{i}"}
            for i, s in enumerate(statuses)
        ]

    def test_all_succeeded(self):
        op = {"StackSetOperation": {"Status": "SUCCEEDED"}}
        inst = {"Summaries": self._make_summaries(["SUCCEEDED", "SUCCEEDED"])}
        result = filter_stackset_status_response(op, inst)
        assert result["status"] == "SUCCEEDED"
        assert result["succeeded"] == 2
        assert result["failed"] == 0

    def test_mixed_statuses(self):
        op = {"StackSetOperation": {"Status": "RUNNING"}}
        inst = {"Summaries": self._make_summaries(["SUCCEEDED", "RUNNING", "FAILED"])}
        result = filter_stackset_status_response(op, inst)
        assert result["running"] == 1
        assert result["failed"] == 1
        assert "failedAccounts" in result

    def test_no_instances(self):
        op = {"StackSetOperation": {"Status": "RUNNING"}}
        result = filter_stackset_status_response(op, {"Summaries": []})
        assert result["totalInstances"] == 0


# ---------------------------------------------------------------------------
# Wrapper unit tests — filter_member_stackset_check
# ---------------------------------------------------------------------------

class TestFilterMemberStacksetCheck:
    def _summary(self, ou_id, detail):
        return {
            "OrganizationalUnitId": ou_id,
            "StackInstanceStatus": {"DetailedStatus": detail},
            "Account": "123456789012",
        }

    def test_not_exists(self):
        result = filter_member_stackset_check(False, {}, "ou-x-1")
        assert result == {"exists": False, "notDeployed": True}

    def test_no_instances_for_ou(self):
        result = filter_member_stackset_check(True, {"Summaries": []}, "ou-x-1")
        assert result == {"exists": True, "notDeployed": True}

    def test_all_succeeded(self):
        summaries = [self._summary("ou-x-1", "SUCCEEDED")] * 3
        result = filter_member_stackset_check(True, {"Summaries": summaries}, "ou-x-1")
        assert result.get("alreadyDeployed") is True
        assert result["succeeded"] == 3

    def test_some_running(self):
        summaries = [
            self._summary("ou-x-1", "SUCCEEDED"),
            self._summary("ou-x-1", "RUNNING"),
        ]
        result = filter_member_stackset_check(True, {"Summaries": summaries}, "ou-x-1")
        assert result["status"] == "RUNNING"

    def test_some_failed(self):
        summaries = [
            self._summary("ou-x-1", "SUCCEEDED"),
            self._summary("ou-x-1", "FAILED"),
        ]
        result = filter_member_stackset_check(True, {"Summaries": summaries}, "ou-x-1")
        assert result["status"] == "FAILED"
        assert "failedAccounts" in result

    def test_different_ou_ignored(self):
        summaries = [self._summary("ou-other", "SUCCEEDED")]
        result = filter_member_stackset_check(True, {"Summaries": summaries}, "ou-x-1")
        assert result == {"exists": True, "notDeployed": True}


# ---------------------------------------------------------------------------
# Unit tests — get_access_role_setup_steps
# ---------------------------------------------------------------------------

_QUICK_CREATE_URL = (
    "https://us-east-1.console.aws.amazon.com/cloudformation/home"
    "?region=us-east-1"
    "#/stacks/create/review"
    "?templateURL=https%3A%2F%2Fs3.amazonaws.com%2Fbucket%2Ftemplate.yaml"
    "&stackName=CommvaultPermissionsStack"
    "&param_HostedInfraRoleArn=arn%3Aaws%3Aiam%3A%3A123456789012%3Arole%2FMetallic"
    "&param_ExternalId=ext-abc"
)
_INFRA_ROLE_ARN = "arn:aws:iam::123456789012:role/MetallicInfrastructureRole"
_EXTERNAL_ID = "ext-abc-123"


class TestGetAccessRoleSetupSteps:
    def _result(self, **kwargs):
        return get_access_role_setup_steps(
            quick_create_url=kwargs.get("quick_create_url", _QUICK_CREATE_URL),
            infra_role_arn=kwargs.get("infra_role_arn", _INFRA_ROLE_ARN),
            external_id=kwargs.get("external_id", _EXTERNAL_ID),
        )

    def test_returns_browser_url(self):
        result = self._result()
        assert result["browserUrl"] == _QUICK_CREATE_URL

    def test_returns_browser_instructions(self):
        result = self._result()
        assert "browserInstructions" in result
        assert "AWS Console" in result["browserInstructions"]

    def test_returns_cli_commands_list(self):
        result = self._result()
        assert isinstance(result["cliCommands"], list)
        assert len(result["cliCommands"]) == 2

    def test_create_stack_command_present(self):
        result = self._result()
        create_cmd = result["cliCommands"][0]["command"]
        assert "aws cloudformation create-stack" in create_cmd
        assert "CAPABILITY_NAMED_IAM" in create_cmd

    def test_template_url_in_create_command(self):
        result = self._result()
        create_cmd = result["cliCommands"][0]["command"]
        assert "s3.amazonaws.com/bucket/template.yaml" in create_cmd

    def test_status_check_command_present(self):
        result = self._result()
        status_cmd = result["cliCommands"][1]["command"]
        assert "describe-stacks" in status_cmd
        assert "StackStatus" in status_cmd

    def test_stack_name_in_commands(self):
        result = self._result()
        for entry in result["cliCommands"]:
            assert _ACCESS_STACK_NAME in entry["command"]

    def test_returns_notes(self):
        result = self._result()
        assert "notes" in result
        assert "CREATE_COMPLETE" in result["notes"]

    def test_no_boto3_calls(self):
        # This should never raise a NoCredentialsError because no AWS SDK is used
        result = self._result()
        assert "error" not in result

    def test_custom_region_and_stack_name(self):
        result = get_access_role_setup_steps(
            quick_create_url=_QUICK_CREATE_URL,
            infra_role_arn=_INFRA_ROLE_ARN,
            region="eu-west-1",
            stack_name="MyCustomStack",
        )
        assert result["region"] == "eu-west-1"
        assert result["stackName"] == "MyCustomStack"
        assert "eu-west-1" in result["cliCommands"][0]["command"]
        assert "MyCustomStack" in result["cliCommands"][0]["command"]


# ---------------------------------------------------------------------------
# Unit tests — get_member_discovery_setup_steps
# ---------------------------------------------------------------------------

_TEMPLATE_URL = "https://s3.amazonaws.com/bucket/member-template.yaml"
_MEMBER_ROLE_ARN = "arn:aws:iam::123456789012:role/MetallicInfrastructureRole"
_MEMBER_USER_ARN = "arn:aws:iam::123456789012:user/CommvaultAssumeRoleUser"


class TestGetMemberDiscoverySetupSteps:
    def _result(self, **kwargs):
        return get_member_discovery_setup_steps(
            template_url=kwargs.get("template_url", _TEMPLATE_URL),
            infra_role_arn=kwargs.get("infra_role_arn", _MEMBER_ROLE_ARN),
            infra_user_arn=kwargs.get("infra_user_arn", _MEMBER_USER_ARN),
        )

    def test_returns_browser_steps(self):
        result = self._result()
        assert "browserSteps" in result
        assert "StackSet" in result["browserSteps"]

    def test_browser_steps_include_template_url(self):
        result = self._result()
        assert _TEMPLATE_URL in result["browserSteps"]

    def test_browser_steps_include_role_arn(self):
        result = self._result()
        assert _MEMBER_ROLE_ARN in result["browserSteps"]

    def test_returns_cli_commands_list(self):
        result = self._result()
        assert isinstance(result["cliCommands"], list)
        assert len(result["cliCommands"]) == 5

    def test_create_stackset_command_present(self):
        result = self._result()
        create_cmd = result["cliCommands"][1]["command"]
        assert "create-stack-set" in create_cmd
        assert "SERVICE_MANAGED" in create_cmd
        assert "DELEGATED_ADMIN" in create_cmd

    def test_create_stack_instances_command_present(self):
        result = self._result()
        instances_cmd = result["cliCommands"][2]["command"]
        assert "create-stack-instances" in instances_cmd
        assert _DEFAULT_TARGET_OU_ID in instances_cmd
        assert "DELEGATED_ADMIN" in instances_cmd

    def test_status_command_present(self):
        result = self._result()
        status_cmd = result["cliCommands"][3]["command"]
        assert "list-stack-instances" in status_cmd
        assert _STACKSET_NAME in status_cmd

    def test_stackset_name_correct(self):
        result = self._result()
        assert result["stackSetName"] == _STACKSET_NAME

    def test_default_ou_id(self):
        result = self._result()
        assert result["targetOuId"] == _DEFAULT_TARGET_OU_ID

    def test_custom_ou_id(self):
        result = get_member_discovery_setup_steps(
            template_url=_TEMPLATE_URL,
            infra_role_arn=_MEMBER_ROLE_ARN,
            infra_user_arn=_MEMBER_USER_ARN,
            target_ou_id="ou-custom-12345",
        )
        assert result["targetOuId"] == "ou-custom-12345"
        assert "ou-custom-12345" in result["cliCommands"][2]["command"]

    def test_returns_notes(self):
        result = self._result()
        assert "notes" in result
        assert "SUCCEEDED" in result["notes"]

    def test_no_boto3_calls(self):
        result = self._result()
        assert "error" not in result


# ---------------------------------------------------------------------------
# MCP registration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_step_tools_registered(mcp_server):
    async with Client(mcp_server) as client:
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        assert "get_access_role_setup_steps" in tool_names
        assert "get_member_discovery_setup_steps" in tool_names
        # Confirm removed tools are gone
        assert "deploy_commvault_access_cft" not in tool_names
        assert "get_commvault_access_cft_status" not in tool_names
        assert "list_org_units" not in tool_names
        assert "check_member_stackset_status" not in tool_names
        assert "deploy_member_account_stackset" not in tool_names
        assert "get_stackset_deployment_status" not in tool_names


# ---------------------------------------------------------------------------
# MCP tool call tests — new step tools (no AWS credentials needed)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_access_role_setup_steps_via_mcp(mcp_server):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_access_role_setup_steps",
            {
                "quick_create_url": _QUICK_CREATE_URL,
                "infra_role_arn": _INFRA_ROLE_ARN,
                "external_id": _EXTERNAL_ID,
            },
        )
        data = extract_response_data(result)
        assert "browserUrl" in data
        assert "cliCommands" in data
        assert isinstance(data["cliCommands"], list)
        assert len(data["cliCommands"]) == 2
        assert "aws cloudformation create-stack" in data["cliCommands"][0]["command"]


@pytest.mark.asyncio
async def test_get_member_discovery_setup_steps_via_mcp(mcp_server):
    async with Client(mcp_server) as client:
        result = await client.call_tool(
            "get_member_discovery_setup_steps",
            {
                "template_url": _TEMPLATE_URL,
                "infra_role_arn": _MEMBER_ROLE_ARN,
                "infra_user_arn": _MEMBER_USER_ARN,
            },
        )
        data = extract_response_data(result)
        assert "browserSteps" in data
        assert "cliCommands" in data
        assert isinstance(data["cliCommands"], list)
        assert len(data["cliCommands"]) == 5
        assert "create-stack-set" in data["cliCommands"][1]["command"]
        assert "create-stack-instances" in data["cliCommands"][2]["command"]
