# --------------------------------------------------------------------------
# Copyright Commvault Systems, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# --------------------------------------------------------------------------

from fastmcp.exceptions import ToolError
from pathlib import Path
from typing import Annotated

import requests

from pydantic import Field

from src.cv_api_client import commvault_api_client
from src.logger import logger
from src.utils import get_env_var
from src.wrappers import filter_virtual_machines_response


_SKILL_FILE = Path(__file__).parent / "aws_recovery_workflow.md"


def _build_job_url(job_id) -> str | None:
    """Build a Command Center deep-link URL for a given job ID using CC_SERVER_URL."""
    if not job_id:
        return None
    try:
        server_url = get_env_var("CC_SERVER_URL", default="").rstrip("/")
        return f"{server_url}/commandcenter/#/jobs/{job_id}"
    except Exception:
        return None


def get_aws_recovery_instructions() -> dict:
    """Load the complete AWS VM recovery workflow guide.

    Call this tool FIRST whenever the user wants to restore or recover an AWS
    virtual machine, including but not limited to:
    - Restore an EC2 instance
    - Recover an AWS VM
    - Trigger an in-place restore for an AWS workload
    - Find and restore a backed-up AWS virtual machine
    - Undo changes on an EC2 instance by restoring from backup

    Returns the full step-by-step guided workflow covering VM listing, user
    selection, UUID resolution, restore submission, and job tracking.
    """
    try:
        instructions = _SKILL_FILE.read_text(encoding="utf-8")
        return {"instructions": instructions}
    except Exception as e:
        logger.error(f"Error reading AWS recovery instructions: {e}")
        raise ToolError(f"Failed to load AWS recovery instructions: {str(e)}")


def list_aws_virtual_machines() -> dict:
    """List all AWS virtual machines known to Commvault.

    This is **step 1** of the AWS VM recovery flow.

    Returns a compact list of VM names and UUIDs. After calling this tool,
    present the VM names to the user as a brief list and ask which VM they
    want to restore. Do NOT show UUIDs to the user.

    Once the user provides a name, match it (case-insensitively if needed)
    to the corresponding UUID from this response and pass that UUID to
    ``restore_aws_virtual_machine``.
    """
    try:
        response = commvault_api_client.get("v4/virtualmachines")
        return filter_virtual_machines_response(response)
    except Exception as e:
        logger.error(f"Error listing AWS virtual machines: {e}")
        return {"error": True, "message": f"Failed to list AWS virtual machines: {str(e)}"}


def restore_aws_virtual_machine(
    vm_uuid: Annotated[
        str,
        Field(
            description=(
                "The UUID (instance ID) of the AWS virtual machine to restore "
                "(e.g. 'i-0fdef8f9f4a8aa3ff'). Obtain this from list_aws_virtual_machines "
                "by matching the user-provided VM name to its UUID. Do not ask the user "
                "for the UUID directly."
            )
        ),
    ],
) -> dict:
    """Trigger an in-place restore of an AWS virtual machine.

    This is **step 2** of the AWS VM recovery flow — call this only after
    ``list_aws_virtual_machines`` has been called and the user has confirmed
    the VM they want to restore.

    The restore is in-place (``inPlaceRestore: true``) and overwrites the
    existing instance (``overwriteVM: true``). After a successful call,
    return the job URL to the user so they can track progress in the
    Command Center UI.
    """
    try:
        payload = {
            "inPlaceRestore": True,
            "overwriteVM": True,
            "vmDestinationInfo": {
                "aws": {
                    "awsInstanceInfoList": [
                        {"instanceId": vm_uuid}
                    ]
                }
            },
        }
        response = commvault_api_client.post(
            f"V4/VM/{vm_uuid}/restore",
            data=payload,
        )
        job_id = (
            response.get("jobIds", [None])[0]
            if isinstance(response.get("jobIds"), list)
            else response.get("jobId")
        )
        return {
            "taskId": response.get("taskId"),
            "jobIds": response.get("jobIds", []),
            "summary": {
                "vmUUID": vm_uuid,
                "jobId": job_id,
                "jobUrl": _build_job_url(job_id),
                "status": "submitted",
            },
        }
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else None
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text if e.response is not None else str(e)
        logger.error(f"HTTP {status_code} error restoring VM {vm_uuid}: {body}")
        return {
            "error": True,
            "httpStatus": status_code,
            "message": f"Failed to restore VM: HTTP {status_code}",
            "apiResponse": body,
        }
    except Exception as e:
        logger.error(f"Error restoring AWS virtual machine {vm_uuid}: {e}")
        return {"error": True, "message": f"Failed to restore AWS virtual machine: {str(e)}"}


AWS_RECOVERY_TOOLS = [
    get_aws_recovery_instructions,
    list_aws_virtual_machines,
    restore_aws_virtual_machine,
]
