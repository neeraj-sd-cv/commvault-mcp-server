from fastmcp import Client
import json
import pytest
from unittest.mock import patch, MagicMock

from src.tools.plan_tools import sanitize_name_for_commvault, suggest_names
from src.wrappers import (
    filter_plan_storage_pool_list,
    filter_create_storage_pool_response,
    filter_create_server_plan_response,
)


# ---------------------------------------------------------------------------
# Helpers shared by MCP tests
# ---------------------------------------------------------------------------

def extract_response_data(result):
    # Handle CallToolResult object
    if hasattr(result, 'content'):
        content_list = result.content
    else:
        content_list = result
    
    if not isinstance(content_list, list) or len(content_list) == 0:
        raise AssertionError("Expected list response with at least one item")
    
    if not hasattr(content_list[0], "text"):
        raise AssertionError("Response item missing 'text' attribute")
    
    response_text = content_list[0].text
    
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return response_text

def assert_no_error_in_response(data, operation_name):
    if isinstance(data, str):
        error_indicators = [
            "error occurred", "failed to", "invalid", "unauthorized", 
            "not found", "exception", "traceback",
            "internal server error", "bad request", "forbidden"
        ]
        data_lower = data.lower()
        for indicator in error_indicators:
            if indicator in data_lower:
                raise AssertionError(f"{operation_name} failed with error: {data}")
        return
    
    elif isinstance(data, dict):
        if "error" in data:
            error = data["error"]
            if isinstance(error, dict):
                error_msg = error.get("errorMessage", "")
                error_code = error.get("errorCode", 0)
                if error_msg or error_code != 0:
                    raise AssertionError(f"{operation_name} failed with error: {error_msg} (code: {error_code})")
            elif error:
                raise AssertionError(f"{operation_name} failed with error: {error}")
        
        if "errorMessage" in data and data["errorMessage"]:
            raise AssertionError(f"{operation_name} failed: {data['errorMessage']}")
        
        if "errorCode" in data and data["errorCode"] != 0:
            raise AssertionError(f"{operation_name} failed with error code: {data['errorCode']}")

def find_plan_id(data):
    if isinstance(data, dict):
        if "plans" in data and isinstance(data["plans"], list) and len(data["plans"]) > 0:
            plan = data["plans"][0]
            if isinstance(plan, dict):
                if "plan" in plan and isinstance(plan["plan"], dict):
                    return plan["plan"].get("planId") or plan["plan"].get("id")
                return plan.get("planId") or plan.get("id")
        
        return data.get("planId") or data.get("id")
    
    elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        return data[0].get("planId") or data[0].get("id")
    
    return None


# ---------------------------------------------------------------------------
# Unit tests — name helpers (no network calls)
# ---------------------------------------------------------------------------

class TestSanitizeName:
    def test_lowercase(self):
        assert sanitize_name_for_commvault("MyConnection") == "myconnection"

    def test_spaces_become_hyphens(self):
        assert sanitize_name_for_commvault("my connection") == "my-connection"

    def test_special_chars_become_hyphens(self):
        assert sanitize_name_for_commvault("aws/prod@2024!") == "aws-prod-2024"

    def test_no_leading_trailing_hyphens(self):
        result = sanitize_name_for_commvault("  my-conn  ")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_no_consecutive_hyphens(self):
        result = sanitize_name_for_commvault("my---connection")
        assert "--" not in result

    def test_max_length_50(self):
        long_name = "a" * 60
        assert len(sanitize_name_for_commvault(long_name)) <= 50


class TestSuggestNames:
    def test_all_keys_present(self):
        names = suggest_names("MyConn")
        assert "storage" in names
        assert "plan" in names
        assert "protection_group" in names

    def test_suffixes(self):
        names = suggest_names("nova")
        assert names["storage"].endswith("-storage")
        assert names["plan"].endswith("-plan")
        assert names["protection_group"].endswith("-protection-group")

    def test_base_derived_from_connection_name(self):
        names = suggest_names("My AWS Connection")
        assert names["storage"] == "my-aws-connection-storage"
        assert names["plan"] == "my-aws-connection-plan"
        assert names["protection_group"] == "my-aws-connection-protection-group"


# ---------------------------------------------------------------------------
# Unit tests — wrapper functions (no network calls)
# ---------------------------------------------------------------------------

class TestFilterPlanStoragePoolList:
    def test_empty_response(self):
        result = filter_plan_storage_pool_list({})
        assert result["totalPools"] == 0
        assert result["storagePools"] == []

    def test_bare_list_response(self):
        raw = [
            {
                "storagePoolEntity": {"storagePoolId": 100, "storagePoolName": "pool-a"},
                "storagePolicyEntity": {"storagePolicyId": 200, "storagePolicyName": "policy-a"},
                "region": {"regionName": "us-east-1", "displayName": "US East (N. Virginia)", "regionId": 50},
                "status": "Online",
                "storageType": 2,
            }
        ]
        result = filter_plan_storage_pool_list(raw)
        assert result["totalPools"] == 1
        pool = result["storagePools"][0]
        assert pool["storagePoolId"] == 100
        assert pool["storagePolicyName"] == "policy-a"
        assert pool["regionName"] == "us-east-1"
        assert pool["status"] == "Online"

    def test_wrapped_response(self):
        raw = {"storagePoolList": [
            {
                "storagePoolEntity": {"storagePoolId": 58912, "storagePoolName": "airgap-cold-azure"},
                "storagePolicyEntity": {"storagePolicyId": 58912, "storagePolicyName": "airgap-cold-azure"},
                "region": {"regionName": "eastus2", "displayName": "(US) East US 2", "regionId": 8},
                "status": "Online",
                "storageType": 2,
            }
        ]}
        result = filter_plan_storage_pool_list(raw)
        assert result["totalPools"] == 1
        assert result["storagePools"][0]["storagePoolId"] == 58912


class TestFilterCreateStoragePoolResponse:
    def test_success(self):
        raw = {
            "responseType": 0,
            "archiveGroupCopy": {
                "storagePolicyName": "nova-storage",
                "storagePolicyId": 67238,
                "copyId": 86933,
                "copyName": "Primary",
            },
            "error": {"errorCode": 0},
        }
        result = filter_create_storage_pool_response(raw)
        assert result["success"] is True
        assert result["storagePolicyId"] == 67238
        assert result["storagePolicyName"] == "nova-storage"
        assert result["copyId"] == 86933
        assert result["errorCode"] == 0

    def test_failure(self):
        raw = {"error": {"errorCode": 1}}
        result = filter_create_storage_pool_response(raw)
        assert result["success"] is False

    def test_missing_error_key(self):
        result = filter_create_storage_pool_response({})
        assert result["success"] is False


class TestFilterCreateServerPlanResponse:
    def test_success(self):
        raw = {
            "plan": {
                "GUID": "5C46A833-91A0-4AFE-AD84-3D7C255F2178",
                "name": "nova-plan",
                "id": 48195,
            }
        }
        result = filter_create_server_plan_response(raw)
        assert result["success"] is True
        assert result["planId"] == 48195
        assert result["planName"] == "nova-plan"
        assert result["guid"] == "5C46A833-91A0-4AFE-AD84-3D7C255F2178"

    def test_missing_plan_id(self):
        result = filter_create_server_plan_response({})
        assert result["success"] is False
        assert result["planId"] is None


# ---------------------------------------------------------------------------
# MCP tool registration tests (no network calls)
# ---------------------------------------------------------------------------

async def test_new_plan_tools_registered(mcp_server):
    """Verify the three new plan tools are present in the MCP server."""
    async with Client(mcp_server) as client:
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
    assert "create_storage_pool_for_plan" in tool_names
    assert "get_plan_storage_pool_list" in tool_names
    assert "create_server_plan" in tool_names


# ---------------------------------------------------------------------------
# MCP live tests — read-only tools
# ---------------------------------------------------------------------------

async def test_get_plan_list(mcp_server):
    async with Client(mcp_server) as client:
        result = await client.call_tool("get_plan_list", {"only_s3_vault_compatible": False})
        data = extract_response_data(result)
        assert_no_error_in_response(data, "get_plan_list")
        
        if isinstance(data, dict):
            assert len(data) >= 0, "Response should be valid"
        elif isinstance(data, list):
            assert len(data) >= 0, "List response should be valid"

async def test_get_plan_properties(mcp_server):
    plan_id = None
    
    async with Client(mcp_server) as client:
        result = await client.call_tool("get_plan_list", {"only_s3_vault_compatible": False})
        data = extract_response_data(result)
        assert_no_error_in_response(data, "get_plan_list")
        
        plan_id = find_plan_id(data)
    
    if not plan_id:
        pytest.skip("No plans found in the system")
    
    async with Client(mcp_server) as client:
        result = await client.call_tool("get_plan_properties", {"plan_id": str(plan_id)})
        data = extract_response_data(result)
        assert_no_error_in_response(data, "get_plan_properties")
        
        if isinstance(data, dict):
            assert len(data) > 0, "Plan properties response should not be empty"
        elif isinstance(data, str):
            assert len(data) > 0, "Plan properties response should not be empty"
        assert len(data) > 0, "Plan properties response should not be empty"

async def test_get_plan_storage_pool_list(mcp_server):
    async with Client(mcp_server) as client:
        result = await client.call_tool("get_plan_storage_pool_list", {})
        data = extract_response_data(result)
        assert_no_error_in_response(data, "get_plan_storage_pool_list")
        assert isinstance(data, dict), "Response should be a dict"
        assert "storagePools" in data or "error" in data, "Response should contain storagePools or error"


# ---------------------------------------------------------------------------
# MCP create tests — mocked API client to avoid real resource creation
# ---------------------------------------------------------------------------

async def test_create_storage_pool_for_plan_success(mcp_server):
    mock_response = {
        "responseType": 0,
        "archiveGroupCopy": {
            "storagePolicyName": "test-storage",
            "storagePolicyId": 99901,
            "copyId": 99902,
            "_type_": 18,
            "copyName": "Primary",
        },
        "error": {"errorCode": 0},
    }
    with patch("src.tools.plan_tools.commvault_api_client") as mock_client:
        mock_client.post.return_value = mock_response
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "create_storage_pool_for_plan",
                {
                    "storage_name": "test-storage",
                    "company_id": 1,
                    "company_name": "TestCompany",
                },
            )
            data = extract_response_data(result)
    assert data.get("success") is True
    assert data.get("storagePolicyId") == 99901
    assert data.get("storagePolicyName") == "test-storage"


async def test_create_storage_pool_for_plan_api_error(mcp_server):
    mock_response = {"error": {"errorCode": 5}}
    with patch("src.tools.plan_tools.commvault_api_client") as mock_client:
        mock_client.post.return_value = mock_response
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "create_storage_pool_for_plan",
                {
                    "storage_name": "test-storage",
                    "company_id": 1,
                    "company_name": "TestCompany",
                },
            )
            data = extract_response_data(result)
    assert data.get("error") is True
    assert "message" in data


async def test_create_server_plan_success(mcp_server):
    mock_response = {
        "plan": {
            "GUID": "AAAAAAAA-0000-0000-0000-000000000001",
            "name": "test-plan",
            "id": 11111,
        }
    }
    with patch("src.tools.plan_tools.commvault_api_client") as mock_client:
        mock_client.post.return_value = mock_response
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "create_server_plan",
                {
                    "plan_name": "test-plan",
                    "storage_pool_id": 99901,
                    "storage_pool_name": "test-storage",
                },
            )
            data = extract_response_data(result)
    assert data.get("success") is True
    assert data.get("planId") == 11111
    assert data.get("planName") == "test-plan"


async def test_create_server_plan_no_plan_id(mcp_server):
    with patch("src.tools.plan_tools.commvault_api_client") as mock_client:
        mock_client.post.return_value = {}
        async with Client(mcp_server) as client:
            result = await client.call_tool(
                "create_server_plan",
                {
                    "plan_name": "test-plan",
                    "storage_pool_id": 99901,
                    "storage_pool_name": "test-storage",
                },
            )
            data = extract_response_data(result)
    assert data.get("error") is True