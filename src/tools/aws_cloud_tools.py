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
from typing import Annotated, Dict, Any
from pydantic import Field

import json

from src.cv_api_client import commvault_api_client
from src.logger import logger
from src.utils import get_env_var
from src.wrappers import (
    filter_aws_permissions_cft_response,
    filter_aws_cloud_connections_response,
    filter_aws_solutions_response,
    filter_eligible_plans_response,
)


_CONNECTION_TYPE_MAP = {
    "organization": "OrganizationLevel",
    "organizationlevel": "OrganizationLevel",
    "account": "AccountLevel",
    "accountlevel": "AccountLevel",
}

_CFT_CONNECTION_TYPE_MAP = {
    "ORGANIZATION": "organization",
    "CLOUD_ACCOUNT": "account",
}


def _build_job_url(job_id) -> str | None:
    """Build a Command Center deep-link URL for a given job ID using CC_SERVER_URL."""
    if not job_id:
        return None
    try:
        server_url = get_env_var("CC_SERVER_URL", default="").rstrip("/")
        return f"{server_url}/commandcenter/#/jobs/{job_id}"
    except Exception:
        return None


def _resolve_connection_type(connection_type: str) -> str:
    resolved = _CONNECTION_TYPE_MAP.get(connection_type.lower())
    if resolved is None:
        raise ValueError(
            f"Invalid connection_type '{connection_type}'. "
            "Use 'organization' or 'account'."
        )
    return resolved


def _build_aws_cloud_payload(
    iam_role_account_id: str,
    connection_type: str,
) -> Dict[str, Any]:
    return {
        "cloudType": "aws",
        "connectionType": connection_type,
        "cloudSpecificConfiguration": {
            "aws": {
                "regions": "default",
                "iamRoleAccountId": iam_role_account_id,
            }
        },
    }


def get_aws_permissions_cft(
    iam_role_account_id: Annotated[
        str,
        Field(description="The AWS account ID of the delegated admin account to onboard."),
    ],
) -> dict:
    """Retrieve the CloudFormation Template (CFT) quick-create links and IAM role details
    needed to set up Commvault permissions in the customer's AWS account.

    This is **step 1** of the AWS onboarding flow.

    After calling this tool:
    1. Present the CFT quick-create URL for the chosen connection type (organization or account)
       to the user and instruct them to open it in the AWS Console to deploy the stack.
    2. Wait for the user to confirm the CFT has been deployed successfully.
    3. Then proceed to call ``validate_aws_cloud_credentials`` to verify the setup.

    If the connection type is "organization", also present the member account setup
    instructions so the user can deploy a StackSet for member accounts later.
    """
    try:
        response = commvault_api_client.get(
            "v4/Cloud/AWS/ExpressConfig/QuickCreateLink/PermissionsCFT",
            params={"iamRoleAccountId": iam_role_account_id},
        )
        return filter_aws_permissions_cft_response(response)
    except Exception as e:
        logger.error(f"Error retrieving AWS permissions CFT: {e}")
        return {"error": True, "message": f"Failed to retrieve AWS permissions CFT: {str(e)}"}


def validate_aws_cloud_credentials(
    iam_role_account_id: Annotated[
        str,
        Field(description="The AWS account ID to validate credentials for."),
    ],
    connection_type: Annotated[
        str,
        Field(description="Connection type: 'organization' for Organization-level or 'account' for single Account-level. Defaults to 'organization'."),
    ] = "organization",
) -> dict:
    """Validate that Commvault can assume the IAM role in the customer's AWS account.

    This is **step 2** of the AWS onboarding flow — call this after the user confirms
    they have deployed the CloudFormation stack from step 1.

    If validation fails, it typically means the CloudFormation stack was not deployed
    correctly or has not finished creating. Ask the user to verify the stack status
    in the AWS Console before retrying.

    After successful validation, present the member account StackSet instructions
    (from step 1 response) to the user if the connection type is 'organization'.
    Then call ``browse_aws_cloud_accounts`` to confirm account discovery works.
    """
    try:
        api_connection_type = _resolve_connection_type(connection_type)
        payload = _build_aws_cloud_payload(iam_role_account_id, api_connection_type)
        response = commvault_api_client.post(
            "V4/Cloud/CloudConnection/credentials/validate",
            data=payload,
        )
        return response
    except ValueError as e:
        return {"error": True, "message": str(e)}
    except Exception as e:
        logger.error(f"Error validating AWS cloud credentials: {e}")
        return {"error": True, "message": f"Failed to validate AWS cloud credentials: {str(e)}"}


def browse_aws_cloud_accounts(
    iam_role_account_id: Annotated[
        str,
        Field(description="The AWS account ID to browse accounts for."),
    ],
    connection_type: Annotated[
        str,
        Field(description="Connection type: 'organization' for Organization-level or 'account' for single Account-level. Defaults to 'organization'."),
    ] = "organization",
) -> dict:
    """Browse the AWS accounts discoverable through the configured IAM role.

    This is **step 3** of the AWS onboarding flow — call this after the user confirms
    they have deployed the member-account StackSet (for organization connections) or
    after successful credential validation (for account connections).

    Use this to confirm that Commvault can discover AWS accounts before creating
    the cloud connection. If no accounts are returned, ask the user to verify their
    StackSet deployment and IAM role configuration.

    After successful browsing, proceed to call ``create_aws_cloud_connection``.
    """
    try:
        api_connection_type = _resolve_connection_type(connection_type)
        payload = _build_aws_cloud_payload(iam_role_account_id, api_connection_type)
        response = commvault_api_client.post(
            "V4/Cloud/CloudConnection/Accounts/Browse",
            data=payload,
        )
        return response
    except ValueError as e:
        return {"error": True, "message": str(e)}
    except Exception as e:
        logger.error(f"Error browsing AWS cloud accounts: {e}")
        return {"error": True, "message": f"Failed to browse AWS cloud accounts: {str(e)}"}


def create_aws_cloud_connection(
    connection_name: Annotated[
        str,
        Field(description="A descriptive name for the new AWS cloud connection."),
    ],
    iam_role_account_id: Annotated[
        str,
        Field(description="The AWS account ID for the cloud connection."),
    ],
    connection_type: Annotated[
        str,
        Field(description="Connection type: 'organization' for Organization-level or 'account' for single Account-level. Defaults to 'organization'."),
    ] = "organization",
    discover_all_accounts: Annotated[
        bool,
        Field(description="Whether to discover all member accounts in the organization. Only applicable for organization-level connections. Defaults to True."),
    ] = True,
) -> dict:
    """Create a new AWS cloud connection in Commvault.

    This is the **final step** of the AWS onboarding flow — call this only after:
    1. ``get_aws_permissions_cft`` — user deployed the CFT
    2. ``validate_aws_cloud_credentials`` — credentials validated successfully
    3. ``browse_aws_cloud_accounts`` — accounts discovered successfully

    Ask the user for a connection name before calling this tool.
    """
    try:
        api_connection_type = _resolve_connection_type(connection_type)

        payload: Dict[str, Any] = {
            "name": connection_name,
            "startDiscoveryJob": True,
            "cloudType": "aws",
            "connectionType": api_connection_type,
            "cloudSpecificConfiguration": {
                "aws": {
                    "regions": "default",
                    "iamRoleAccountId": iam_role_account_id,
                }
            },
        }

        if api_connection_type == "OrganizationLevel":
            payload["cloudSpecificConfiguration"]["aws"]["organizationConfiguration"] = {
                "content": {
                    "accounts": [],
                    "discoverAllAccounts": discover_all_accounts,
                },
                "enableOwnerDetection": False,
            }

        response = commvault_api_client.post(
            "V4/Cloud/CloudConnection",
            data=payload,
        )
        result = dict(response)
        discovery_job_id = (
            response.get("jobId")
            or (response.get("jobIds", [None])[0] if isinstance(response.get("jobIds"), list) else None)
            or response.get("discoveryJobId")
        )
        result["summary"] = {
            "connectionName": connection_name,
            "connectionId": response.get("id") or response.get("cloudConnectionId"),
            "discoveryStarted": True,
            "discoveryJobId": discovery_job_id,
            "discoveryJobUrl": _build_job_url(discovery_job_id),
        }
        return result
    except ValueError as e:
        return {"error": True, "message": str(e)}
    except Exception as e:
        logger.error(f"Error creating AWS cloud connection: {e}")
        return {"error": True, "message": f"Failed to create AWS cloud connection: {str(e)}"}


def list_aws_cloud_connections() -> dict:
    """List all existing AWS cloud connections in Commvault.

    Call this as **step 1** of the AWS protection group setup flow.

    Present the returned connections to the user and ask them to select one.
    Carry the chosen connection's ``id``, ``name``, ``connectionType``,
    ``companyName``, and ``companyId`` forward for use in
    ``create_aws_protection_group``.
    """
    try:
        response = commvault_api_client.get(
            "V4/Cloud/CloudConnection",
            params={"vendor": "aws"},
        )
        return filter_aws_cloud_connections_response(response)
    except Exception as e:
        logger.error(f"Error listing AWS cloud connections: {e}")
        return {"error": True, "message": f"Failed to list AWS cloud connections: {str(e)}"}


def list_aws_workloads() -> dict:
    """List the AWS workload types available for protection in Commvault.

    Call this as **step 2** of the AWS protection group setup flow, after the
    user has selected a cloud connection.

    Present the workloads grouped by category (e.g. File Servers, Virtualization,
    Databases) and ask the user which workloads they want to protect. The user
    may select any combination across categories.

    Carry the selected workloads (``id`` and ``name`` for each) forward for use
    in ``create_aws_protection_group``.
    """
    try:
        response = commvault_api_client.get(
            "v4/solutions",
            params={"vendor": "aws", "filter": 7},
        )
        return filter_aws_solutions_response(response)
    except Exception as e:
        logger.error(f"Error listing AWS workloads: {e}")
        return {"error": True, "message": f"Failed to list AWS workloads: {str(e)}"}


def list_eligible_plans() -> dict:
    """List the backup plans eligible for an AWS protection group.

    Call this as **step 3** of the AWS protection group setup flow, after the
    user has selected workloads.

    Present the plans (name, summary, RPO, number of copies) and ask the user
    to choose one. Carry the chosen plan's ``planId`` forward for use in
    ``create_aws_protection_group``.
    """
    try:
        response = commvault_api_client.get(
            "v2/plan/Eligible",
            params={
                "appId": 104,
                "filterStoragePools": "true",
                "operationType": 3,
                "storageSubType": 6,
                "fl": (
                    "plans.plan.planId,plans.plan.planName,plans.numCopies,"
                    "plans.rpoInMinutes,plans.parent,plans.subtype,plans.type,"
                    "plans.plan.planSummary,plans.storage.copy"
                ),
                "sort": "plans.plan.planName:1",
            },
        )
        return filter_eligible_plans_response(response)
    except Exception as e:
        logger.error(f"Error listing eligible plans: {e}")
        return {"error": True, "message": f"Failed to list eligible plans: {str(e)}"}


def create_aws_protection_group(
    name: Annotated[
        str,
        Field(description="A descriptive name for the new protection group."),
    ],
    cloud_connection_id: Annotated[
        int,
        Field(description="The numeric ID of the selected cloud connection (from list_aws_cloud_connections)."),
    ],
    cloud_connection_name: Annotated[
        str,
        Field(description="The name of the selected cloud connection."),
    ],
    connection_type: Annotated[
        str,
        Field(description="The connectionType of the selected cloud connection (e.g. 'OrganizationLevel')."),
    ],
    company_name: Annotated[
        str,
        Field(description="The company name from the selected cloud connection."),
    ],
    company_id: Annotated[
        int,
        Field(description="The company ID from the selected cloud connection."),
    ],
    plan_id: Annotated[
        int,
        Field(description="The planId of the selected backup plan (from list_eligible_plans)."),
    ],
    workloads_json: Annotated[
        str,
        Field(
            description=(
                'JSON array of selected workloads, each with "id" and "name" keys. '
                'Example: [{"id": 8004, "name": "Amazon Aurora and RDS Snapshot"}, '
                '{"id": 10301, "name": "Amazon EC2"}]'
            )
        ),
    ],
    all_cloud_accounts: Annotated[
        bool,
        Field(description="Whether to protect all cloud accounts under the connection. Defaults to True."),
    ] = True,
) -> dict:
    """Create a new AWS protection group in Commvault.

    This is the **final step** of the AWS protection group setup flow — call
    this only after completing all prerequisite steps:
    1. ``list_aws_cloud_connections`` — user selected a connection
    2. ``list_aws_workloads`` — user selected workloads to protect
    3. ``list_eligible_plans`` — user selected a backup plan

    Ask the user for a protection group name before calling this tool.
    """
    try:
        workloads_list = json.loads(workloads_json)
        workloads_payload = [
            {"workload": {"id": w["id"], "name": w["name"]}}
            for w in workloads_list
        ]

        payload: Dict[str, Any] = {
            "name": name,
            "cloudConnection": {
                "id": cloud_connection_id,
                "name": cloud_connection_name,
                "displayName": cloud_connection_name,
                "cloudType": "aws",
                "connectionType": connection_type,
                "company": {
                    "name": company_name,
                    "id": company_id,
                },
                "selected": True,
                "hidden": False,
            },
            "cloudConnectionId": None,
            "vendorFromPath": "aws",
            "disableConnections": False,
            "allCloudAccounts": all_cloud_accounts,
            "plan": {"id": plan_id},
            "cloudAccounts": [],
            "content": [],
            "workloads": workloads_payload,
        }

        response = commvault_api_client.post(
            "V4/protectiongroup/aws",
            data=payload,
        )
        result = dict(response)
        result["summary"] = {
            "protectionGroupName": name,
            "protectionGroupId": response.get("id") or response.get("subClientId"),
            "workloadCount": len(workloads_list),
            "planId": plan_id,
        }
        return result
    except json.JSONDecodeError as e:
        return {"error": True, "message": f"Invalid workloads_json format: {str(e)}"}
    except Exception as e:
        logger.error(f"Error creating AWS protection group: {e}")
        return {"error": True, "message": f"Failed to create AWS protection group: {str(e)}"}


def start_aws_protection_group_backup(
    protection_group_id: Annotated[
        int,
        Field(
            description=(
                "The protection group ID returned by create_aws_protection_group. "
                "This is the Subclient ID used in the backup API path; do not pass "
                "the cloud connection ID or plan ID."
            )
        ),
    ],
    backup_level: Annotated[
        str,
        Field(description="Backup level to start. Defaults to FULL."),
    ] = "FULL",
    notify_user_on_job_completion: Annotated[
        bool,
        Field(description="Whether to notify the user when the job completes. Defaults to True."),
    ] = True,
    job_description: Annotated[
        str,
        Field(description="Description to attach to the backup job."),
    ] = "Initial full after user onboarding",
) -> dict:
    """Start a backup job for an AWS protection group.

    Call this only after ``create_aws_protection_group`` succeeds and the user
    confirms that the initial backup job can be started.

    The ``protection_group_id`` must be the ID of the created protection group
    returned by ``create_aws_protection_group``. It is used as the Subclient ID
    in ``Subclient/{protection_group_id}/action/backup``. Do not use the cloud
    connection ID or backup plan ID here.
    """
    try:
        response = commvault_api_client.post(
            f"Subclient/{protection_group_id}/action/backup",
            params={
                "backupLevel": backup_level,
                "notifyUserOnJobCompletion": str(notify_user_on_job_completion).lower(),
                "jobDescription": job_description,
            },
        )
        result = dict(response)
        job_id = (
            response.get("jobIds", [None])[0]
            if isinstance(response.get("jobIds"), list)
            else response.get("jobId")
        )
        result["summary"] = {
            "jobId": job_id,
            "jobUrl": _build_job_url(job_id),
            "backupLevel": backup_level,
            "status": "submitted",
        }
        return result
    except Exception as e:
        logger.error(f"Error starting AWS protection group backup: {e}")
        return {
            "error": True,
            "message": f"Failed to start AWS protection group backup: {str(e)}",
        }


_SKILL_FILE = Path(__file__).parent.parent.parent / ".claude" / "skills" / "aws-onboarding" / "SKILL.md"


def get_aws_onboarding_instructions() -> dict:
    """Return the step-by-step AWS onboarding workflow instructions.

    Call this tool at the start of any AWS onboarding session to load the
    complete guided workflow into context. The instructions cover all steps
    from retrieving CFT links through creating the final cloud connection.
    """
    try:
        instructions = _SKILL_FILE.read_text(encoding="utf-8")
        return {"instructions": instructions}
    except Exception as e:
        logger.error(f"Error reading AWS onboarding instructions: {e}")
        raise ToolError(f"Failed to load AWS onboarding instructions: {str(e)}")


AWS_CLOUD_TOOLS = [
    get_aws_onboarding_instructions,
    get_aws_permissions_cft,
    validate_aws_cloud_credentials,
    browse_aws_cloud_accounts,
    create_aws_cloud_connection,
    list_aws_cloud_connections,
    list_aws_workloads,
    list_eligible_plans,
    create_aws_protection_group,
    start_aws_protection_group_backup,
]
