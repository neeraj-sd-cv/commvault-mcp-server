"""Unit tests for the AWS VM recovery tools and wrapper."""

import json
import pytest
from unittest.mock import patch, MagicMock

from src.wrappers import filter_virtual_machines_response
from src.tools.aws_recovery_tools import (
    list_aws_virtual_machines,
    restore_aws_virtual_machine,
    get_aws_recovery_instructions,
    load_aws_restore_guide,
    _build_job_url,
)


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

_VM_API_RESPONSE = {
    "virtualMachinesCount": 2,
    "virtualMachines": [
        {
            "name": "prod-web-server",
            "vendor": "AMAZON",
            "displayName": "prod-web-server",
            "cloudVendor": "AWS",
            "UUID": "i-0fdef8f9f4a8aa3ff",
            "status": "PROTECTED",
        },
        {
            "name": "staging-db",
            "vendor": "AMAZON",
            "displayName": "staging-db",
            "cloudVendor": "AWS",
            "UUID": "i-035d6e1e157464034",
            "status": "NOT_PROTECTED",
        },
    ],
}

_RESTORE_API_RESPONSE = {
    "taskId": 1927766,
    "jobIds": ["12612401"],
}


# ---------------------------------------------------------------------------
# Wrapper unit tests — filter_virtual_machines_response
# ---------------------------------------------------------------------------

class TestFilterVirtualMachinesResponse:
    def test_extracts_name_and_uuid(self):
        result = filter_virtual_machines_response(_VM_API_RESPONSE)
        assert result["virtualMachines"][0] == {
            "name": "prod-web-server",
            "UUID": "i-0fdef8f9f4a8aa3ff",
        }

    def test_extracts_count(self):
        result = filter_virtual_machines_response(_VM_API_RESPONSE)
        assert result["virtualMachinesCount"] == 2

    def test_no_extra_fields(self):
        result = filter_virtual_machines_response(_VM_API_RESPONSE)
        for vm in result["virtualMachines"]:
            assert set(vm.keys()) == {"name", "UUID"}

    def test_all_vms_included(self):
        result = filter_virtual_machines_response(_VM_API_RESPONSE)
        assert len(result["virtualMachines"]) == 2
        names = [vm["name"] for vm in result["virtualMachines"]]
        assert "prod-web-server" in names
        assert "staging-db" in names

    def test_empty_response(self):
        result = filter_virtual_machines_response({"virtualMachinesCount": 0, "virtualMachines": []})
        assert result["virtualMachinesCount"] == 0
        assert result["virtualMachines"] == []

    def test_count_falls_back_to_list_length(self):
        response = {"virtualMachines": [{"name": "vm-a", "UUID": "i-aaa"}]}
        result = filter_virtual_machines_response(response)
        assert result["virtualMachinesCount"] == 1

    def test_missing_fields_produce_empty_strings(self):
        response = {"virtualMachines": [{"name": "vm-no-uuid"}]}
        result = filter_virtual_machines_response(response)
        assert result["virtualMachines"][0] == {"name": "vm-no-uuid", "UUID": ""}


# ---------------------------------------------------------------------------
# Tool tests — list_aws_virtual_machines
# ---------------------------------------------------------------------------

class TestListAwsVirtualMachines:
    def test_calls_correct_endpoint(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.get.return_value = _VM_API_RESPONSE
            list_aws_virtual_machines()
            mock_client.get.assert_called_once_with("v4/virtualmachines")

    def test_returns_filtered_response(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.get.return_value = _VM_API_RESPONSE
            result = list_aws_virtual_machines()
            assert result["virtualMachinesCount"] == 2
            assert all(set(vm.keys()) == {"name", "UUID"} for vm in result["virtualMachines"])

    def test_returns_error_dict_on_exception(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.get.side_effect = RuntimeError("network failure")
            result = list_aws_virtual_machines()
            assert result["error"] is True
            assert "Failed to list AWS virtual machines" in result["message"]


# ---------------------------------------------------------------------------
# Tool tests — restore_aws_virtual_machine
# ---------------------------------------------------------------------------

class TestRestoreAwsVirtualMachine:
    _UUID = "i-0fdef8f9f4a8aa3ff"

    def test_calls_correct_endpoint(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.post.return_value = _RESTORE_API_RESPONSE
            restore_aws_virtual_machine(self._UUID)
            call_args = mock_client.post.call_args
            assert call_args[0][0] == f"V4/VM/{self._UUID}/restore"

    def test_payload_is_in_place_overwrite(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.post.return_value = _RESTORE_API_RESPONSE
            restore_aws_virtual_machine(self._UUID)
            payload = mock_client.post.call_args[1]["data"]
            assert payload["inPlaceRestore"] is True
            assert payload["overwriteVM"] is True

    def test_payload_contains_instance_id(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.post.return_value = _RESTORE_API_RESPONSE
            restore_aws_virtual_machine(self._UUID)
            payload = mock_client.post.call_args[1]["data"]
            instance_list = payload["vmDestinationInfo"]["aws"]["awsInstanceInfoList"]
            assert instance_list == [{"instanceId": self._UUID}]

    def test_returns_task_and_job_ids(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.post.return_value = _RESTORE_API_RESPONSE
            result = restore_aws_virtual_machine(self._UUID)
            assert result["taskId"] == 1927766
            assert result["jobIds"] == ["12612401"]

    def test_summary_contains_job_id(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.post.return_value = _RESTORE_API_RESPONSE
            result = restore_aws_virtual_machine(self._UUID)
            assert result["summary"]["jobId"] == "12612401"
            assert result["summary"]["vmUUID"] == self._UUID
            assert result["summary"]["status"] == "submitted"

    def test_summary_contains_job_url(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.post.return_value = _RESTORE_API_RESPONSE
            with patch.dict("os.environ", {"CC_SERVER_URL": "https://myserver.example.com"}):
                result = restore_aws_virtual_machine(self._UUID)
            assert result["summary"]["jobUrl"] == "https://myserver.example.com/commandcenter/#/jobs/12612401"

    def test_returns_error_dict_on_http_error(self):
        import requests as req_lib
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            http_err = req_lib.exceptions.HTTPError(response=MagicMock(
                status_code=403,
                json=MagicMock(return_value={"errorMessage": "Forbidden"}),
            ))
            mock_client.post.side_effect = http_err
            result = restore_aws_virtual_machine(self._UUID)
            assert result["error"] is True
            assert result["httpStatus"] == 403

    def test_returns_error_dict_on_generic_exception(self):
        with patch("src.tools.aws_recovery_tools.commvault_api_client") as mock_client:
            mock_client.post.side_effect = RuntimeError("timeout")
            result = restore_aws_virtual_machine(self._UUID)
            assert result["error"] is True
            assert "Failed to restore AWS virtual machine" in result["message"]


# ---------------------------------------------------------------------------
# Tool tests — get_aws_recovery_instructions
# ---------------------------------------------------------------------------

class TestGetAwsRecoveryInstructions:
    def test_loads_markdown_without_error(self):
        result = get_aws_recovery_instructions()
        assert "instructions" in result
        assert len(result["instructions"]) > 0

    def test_instructions_contain_key_sections(self):
        result = get_aws_recovery_instructions()
        text = result["instructions"]
        assert "list_aws_virtual_machines" in text
        assert "restore_aws_virtual_machine" in text


# ---------------------------------------------------------------------------
# Tool tests — load_aws_restore_guide
# ---------------------------------------------------------------------------

class TestLoadAwsRestoreGuide:
    def test_loads_markdown_without_error(self):
        result = load_aws_restore_guide()
        assert "instructions" in result
        assert len(result["instructions"]) > 0

    def test_returns_same_content_as_recovery_instructions(self):
        assert load_aws_restore_guide() == get_aws_recovery_instructions()


# ---------------------------------------------------------------------------
# Helper tests — _build_job_url
# ---------------------------------------------------------------------------

class TestBuildJobUrl:
    def test_builds_correct_url(self):
        with patch.dict("os.environ", {"CC_SERVER_URL": "https://cv.example.com"}):
            url = _build_job_url("99999")
        assert url == "https://cv.example.com/commandcenter/#/jobs/99999"

    def test_strips_trailing_slash(self):
        with patch.dict("os.environ", {"CC_SERVER_URL": "https://cv.example.com/"}):
            url = _build_job_url("1")
        assert url == "https://cv.example.com/commandcenter/#/jobs/1"

    def test_returns_none_for_falsy_job_id(self):
        assert _build_job_url(None) is None
        assert _build_job_url("") is None
        assert _build_job_url(0) is None
