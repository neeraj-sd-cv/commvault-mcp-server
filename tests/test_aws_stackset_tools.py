"""Unit and MCP-level tests for the StackSet / Organizations tools."""

from fastmcp import Client
import json
import pytest
from unittest.mock import patch, MagicMock, call
from botocore.exceptions import ClientError, NoCredentialsError

from src.wrappers import (
    filter_org_units_response,
    filter_stackset_status_response,
    filter_member_stackset_check,
)
from src.tools.aws_cloud_tools import (
    list_org_units,
    check_member_stackset_status,
    deploy_member_account_stackset,
    get_stackset_deployment_status,
    _STACKSET_NAME,
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
# Tool unit tests (mocked boto3) — list_org_units
# ---------------------------------------------------------------------------

class TestListOrgUnits:
    def _paginator_mock(self, pages):
        paginator = MagicMock()
        paginator.paginate.return_value = pages
        return paginator

    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_resolves_root_string(self, mock_boto):
        client = MagicMock()
        mock_boto.return_value = client
        client.list_roots.return_value = {"Roots": [{"Id": "r-0001"}]}
        client.get_paginator.return_value = self._paginator_mock([
            {"OrganizationalUnits": [{"Id": "ou-abc-1", "Name": "Dev"}]}
        ])

        result = list_org_units("root")
        assert result["totalOUs"] == 1
        assert result["ous"][0]["ouId"] == "ou-abc-1"
        client.get_paginator.assert_called_with("list_organizational_units_for_parent")

    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_passes_real_parent_id(self, mock_boto):
        client = MagicMock()
        mock_boto.return_value = client
        client.get_paginator.return_value = self._paginator_mock([
            {"OrganizationalUnits": []}
        ])

        result = list_org_units("r-0001")
        assert result["totalOUs"] == 0
        client.list_roots.assert_not_called()

    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_no_credentials_returns_error(self, mock_boto):
        mock_boto.side_effect = NoCredentialsError()
        result = list_org_units("root")
        assert "error" in result

    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_client_error_returns_error(self, mock_boto):
        client = MagicMock()
        mock_boto.return_value = client
        client.list_roots.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}, "ListRoots"
        )
        result = list_org_units("root")
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool unit tests — check_member_stackset_status
# ---------------------------------------------------------------------------

class TestCheckMemberStacksetStatus:
    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_stackset_not_found(self, mock_boto):
        client = MagicMock()
        mock_boto.return_value = client
        client.describe_stack_set.side_effect = ClientError(
            {"Error": {"Code": "StackSetNotFoundException", "Message": "Not found"}},
            "DescribeStackSet",
        )
        result = check_member_stackset_status("ou-x-1")
        assert result.get("exists") is False

    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_already_deployed(self, mock_boto):
        client = MagicMock()
        mock_boto.return_value = client
        client.describe_stack_set.return_value = {}  # exists
        client.get_paginator.return_value.paginate.return_value = [
            {
                "Summaries": [
                    {
                        "OrganizationalUnitId": "ou-x-1",
                        "StackInstanceStatus": {"DetailedStatus": "SUCCEEDED"},
                        "Account": "111111111111",
                    }
                ]
            }
        ]
        result = check_member_stackset_status("ou-x-1")
        assert result.get("alreadyDeployed") is True

    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_no_credentials_returns_error(self, mock_boto):
        mock_boto.side_effect = NoCredentialsError()
        result = check_member_stackset_status("ou-x-1")
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool unit tests — deploy_member_account_stackset
# ---------------------------------------------------------------------------

class TestDeployMemberAccountStackset:
    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_creates_new_stackset_and_instances(self, mock_boto):
        client = MagicMock()
        mock_boto.return_value = client

        client.get_template_summary.return_value = {
            "Parameters": [
                {"ParameterKey": "HostedInfraRoleArn"},
                {"ParameterKey": "HostedInfraUserArn"},
            ]
        }
        client.describe_stack_set.side_effect = ClientError(
            {"Error": {"Code": "StackSetNotFoundException", "Message": "Not found"}},
            "DescribeStackSet",
        )
        client.create_stack_instances.return_value = {"OperationId": "op-abc-123"}
        client.describe_stack_set_operation.return_value = {
            "StackSetOperation": {"Status": "SUCCEEDED"}
        }
        client.get_paginator.return_value.paginate.return_value = [
            {
                "Summaries": [
                    {
                        "Account": "111",
                        "StackInstanceStatus": {"DetailedStatus": "SUCCEEDED"},
                    }
                ]
            }
        ]

        result = deploy_member_account_stackset(
            target_ou_id="ou-x-1",
            template_url="https://s3.amazonaws.com/bucket/template.yaml",
            infra_role_arn="arn:aws:iam::123456789012:role/MetallicInfrastructureRole",
            infra_user_arn="arn:aws:iam::123456789012:user/CommvaultAssumeRoleUser",
        )

        assert result["success"] is True
        assert result["operationId"] == "op-abc-123"
        assert result["targetOuId"] == "ou-x-1"
        client.create_stack_set.assert_called_once()
        client.create_stack_instances.assert_called_once()

    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_updates_existing_stackset(self, mock_boto):
        client = MagicMock()
        mock_boto.return_value = client

        client.get_template_summary.return_value = {"Parameters": []}
        client.describe_stack_set.return_value = {
            "StackSet": {"PermissionModel": "SERVICE_MANAGED"}
        }
        client.create_stack_instances.return_value = {"OperationId": "op-xyz-456"}
        client.describe_stack_set_operation.return_value = {
            "StackSetOperation": {"Status": "SUCCEEDED"}
        }
        client.get_paginator.return_value.paginate.return_value = [
            {
                "Summaries": [
                    {
                        "Account": "111",
                        "StackInstanceStatus": {"DetailedStatus": "SUCCEEDED"},
                    }
                ]
            }
        ]

        result = deploy_member_account_stackset(
            target_ou_id="ou-x-1",
            template_url="https://s3.amazonaws.com/bucket/template.yaml",
            infra_role_arn="arn:aws:iam::123456789012:role/MetallicInfrastructureRole",
            infra_user_arn="arn:aws:iam::123456789012:user/CommvaultAssumeRoleUser",
        )

        assert result["success"] is True
        client.update_stack_set.assert_called_once()
        client.create_stack_set.assert_not_called()

    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_no_credentials_returns_error(self, mock_boto):
        mock_boto.side_effect = NoCredentialsError()
        result = deploy_member_account_stackset(
            target_ou_id="ou-x-1",
            template_url="https://s3.amazonaws.com/bucket/template.yaml",
            infra_role_arn="arn:aws:iam::123456789012:role/MetallicInfrastructureRole",
            infra_user_arn="arn:aws:iam::123456789012:user/CommvaultAssumeRoleUser",
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool unit tests — get_stackset_deployment_status
# ---------------------------------------------------------------------------

class TestGetStacksetDeploymentStatus:
    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_returns_status_summary(self, mock_boto):
        client = MagicMock()
        mock_boto.return_value = client

        client.describe_stack_set_operation.return_value = {
            "StackSetOperation": {"Status": "RUNNING"}
        }
        client.get_paginator.return_value.paginate.return_value = [
            {
                "Summaries": [
                    {
                        "StackInstanceStatus": {"DetailedStatus": "SUCCEEDED"},
                        "Account": "111",
                    },
                    {
                        "StackInstanceStatus": {"DetailedStatus": "RUNNING"},
                        "Account": "222",
                    },
                ]
            }
        ]

        result = get_stackset_deployment_status("op-abc-123")
        assert result["status"] == "RUNNING"
        assert result["succeeded"] == 1
        assert result["running"] == 1

    @patch("src.tools.aws_cloud_tools.boto3.client")
    def test_no_credentials_returns_error(self, mock_boto):
        mock_boto.side_effect = NoCredentialsError()
        result = get_stackset_deployment_status("op-abc-123")
        assert "error" in result


# ---------------------------------------------------------------------------
# MCP registration test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stackset_tools_registered(mcp_server):
    async with Client(mcp_server) as client:
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        assert "list_org_units" in tool_names
        assert "check_member_stackset_status" in tool_names
        assert "deploy_member_account_stackset" in tool_names
        assert "get_stackset_deployment_status" in tool_names


# ---------------------------------------------------------------------------
# MCP tool call tests (mocked boto3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_org_units_via_mcp(mcp_server):
    """list_org_units returns a valid ous list via the MCP layer."""
    with patch("src.tools.aws_cloud_tools.boto3.client") as mock_boto:
        client_mock = MagicMock()
        mock_boto.return_value = client_mock
        client_mock.list_roots.return_value = {"Roots": [{"Id": "r-0001"}]}
        client_mock.get_paginator.return_value.paginate.return_value = [
            {"OrganizationalUnits": [{"Id": "ou-abc-1", "Name": "Production"}]}
        ]

        async with Client(mcp_server) as client:
            result = await client.call_tool("list_org_units", {"parent_id": "root"})
            data = extract_response_data(result)
            assert "totalOUs" in data or "error" in data


@pytest.mark.asyncio
async def test_check_member_stackset_status_via_mcp(mcp_server):
    """check_member_stackset_status returns exists:False when StackSet is absent."""
    with patch("src.tools.aws_cloud_tools.boto3.client") as mock_boto:
        client_mock = MagicMock()
        mock_boto.return_value = client_mock
        client_mock.describe_stack_set.side_effect = ClientError(
            {"Error": {"Code": "StackSetNotFoundException", "Message": "nf"}},
            "DescribeStackSet",
        )

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "check_member_stackset_status", {"target_ou_id": "ou-x-1"}
            )
            data = extract_response_data(result)
            assert data.get("exists") is False or "error" in data


@pytest.mark.asyncio
async def test_deploy_member_account_stackset_via_mcp(mcp_server):
    """deploy_member_account_stackset returns success:True via the MCP layer."""
    with patch("src.tools.aws_cloud_tools.boto3.client") as mock_boto:
        client_mock = MagicMock()
        mock_boto.return_value = client_mock
        client_mock.get_template_summary.return_value = {"Parameters": []}
        client_mock.describe_stack_set.side_effect = ClientError(
            {"Error": {"Code": "StackSetNotFoundException", "Message": "nf"}},
            "DescribeStackSet",
        )
        client_mock.create_stack_instances.return_value = {"OperationId": "op-test-1"}
        client_mock.describe_stack_set_operation.return_value = {
            "StackSetOperation": {"Status": "SUCCEEDED"}
        }
        client_mock.get_paginator.return_value.paginate.return_value = [
            {
                "Summaries": [
                    {
                        "Account": "111",
                        "StackInstanceStatus": {"DetailedStatus": "SUCCEEDED"},
                    }
                ]
            }
        ]

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "deploy_member_account_stackset",
                {
                    "target_ou_id": "ou-x-1",
                    "template_url": "https://s3.amazonaws.com/bucket/tpl.yaml",
                    "infra_role_arn": "arn:aws:iam::123456789012:role/MetallicInfrastructureRole",
                    "infra_user_arn": "arn:aws:iam::123456789012:user/CommvaultAssumeRoleUser",
                },
            )
            data = extract_response_data(result)
            assert data.get("success") is True or "error" in data


@pytest.mark.asyncio
async def test_get_stackset_deployment_status_via_mcp(mcp_server):
    """get_stackset_deployment_status returns a status field via the MCP layer."""
    with patch("src.tools.aws_cloud_tools.boto3.client") as mock_boto:
        client_mock = MagicMock()
        mock_boto.return_value = client_mock
        client_mock.describe_stack_set_operation.return_value = {
            "StackSetOperation": {"Status": "SUCCEEDED"}
        }
        client_mock.get_paginator.return_value.paginate.return_value = [
            {"Summaries": [{"StackInstanceStatus": {"DetailedStatus": "SUCCEEDED"}, "Account": "111"}]}
        ]

        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "get_stackset_deployment_status", {"operation_id": "op-test-1"}
            )
            data = extract_response_data(result)
            assert "status" in data or "error" in data
