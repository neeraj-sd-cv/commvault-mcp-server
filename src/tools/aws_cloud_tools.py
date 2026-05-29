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
import time

from src.cv_api_client import commvault_api_client
from src.logger import logger
from src.utils import get_env_var
import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from src.wrappers import (
    filter_aws_permissions_cft_response,
    filter_aws_cloud_connections_response,
    filter_aws_solutions_response,
    filter_eligible_plans_response,
    filter_org_units_response,
    filter_stackset_status_response,
    filter_member_stackset_check,
)


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


def _build_aws_cloud_payload(iam_role_account_id: str) -> Dict[str, Any]:
    return {
        "cloudType": "aws",
        "connectionType": "OrganizationLevel",
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
    1. Immediately call ``deploy_commvault_access_cft`` using the values from this response:
       - ``template_url``  → ``connectionTypes.organization.cftQuickCreateUrl``
       - ``infra_role_arn`` → ``connectionTypes.organization.iamRoleArn``
       - ``external_id``   → ``connectionTypes.organization.externalId``
       Do NOT ask the user to open any URL manually — call the deploy tool directly.
    2. Poll ``get_commvault_access_cft_status`` until the stack reaches CREATE_COMPLETE.
    3. Call ``validate_aws_cloud_credentials`` to verify the setup.
    4. Call ``deploy_member_account_stackset`` with:
       - ``template_url``   → ``connectionTypes.organization.memberAccountSetup.templateUrl``
       - ``infra_role_arn`` → ``connectionTypes.organization.memberAccountSetup.hostedInfraRoleArn``
       - ``infra_user_arn`` → ``connectionTypes.organization.memberAccountSetup.hostedInfraUserArn``
       Do not ask the user to deploy the StackSet manually.
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
) -> dict:
    """Validate that Commvault can assume the IAM role in the customer's AWS account.

    This is **step 2** of the AWS onboarding flow — call this after the user confirms
    they have deployed the CloudFormation stack from step 1.

    If validation fails, it typically means the CloudFormation stack was not deployed
    correctly or has not finished creating. Ask the user to verify the stack status
    in the AWS Console before retrying.

    After successful validation, call ``deploy_member_account_stackset`` to set up
    member-account discovery, then ``browse_aws_cloud_accounts`` to confirm account
    discovery works.
    """
    try:
        payload = _build_aws_cloud_payload(iam_role_account_id)
        response = commvault_api_client.post(
            "V4/Cloud/CloudConnection/credentials/validate",
            data=payload,
        )
        return response
    except Exception as e:
        logger.error(f"Error validating AWS cloud credentials: {e}")
        return {"error": True, "message": f"Failed to validate AWS cloud credentials: {str(e)}"}


def browse_aws_cloud_accounts(
    iam_role_account_id: Annotated[
        str,
        Field(description="The AWS account ID to browse accounts for."),
    ],
) -> dict:
    """Browse the AWS accounts discoverable through the configured IAM role.

    This is **step 3** of the AWS onboarding flow — call this after the
    member-account StackSet has been deployed successfully.

    Use this to confirm that Commvault can discover AWS accounts before creating
    the cloud connection. If no accounts are returned, ask the user to verify their
    StackSet deployment and IAM role configuration.

    After successful browsing, proceed to call ``create_aws_cloud_connection``.
    """
    try:
        payload = _build_aws_cloud_payload(iam_role_account_id)
        response = commvault_api_client.post(
            "V4/Cloud/CloudConnection/Accounts/Browse",
            data=payload,
        )
        return response
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
    discover_all_accounts: Annotated[
        bool,
        Field(description="Whether to discover all member accounts in the organization. Defaults to True."),
    ] = True,
) -> dict:
    """Create a new AWS organization-level AWS cloud connection in Commvault.

    This is the **final step** of the AWS onboarding flow — call this only after:
    1. ``get_aws_permissions_cft`` — CFT deployed
    2. ``validate_aws_cloud_credentials`` — credentials validated successfully
    3. ``browse_aws_cloud_accounts`` — accounts discovered successfully

    Ask the user for a connection name before calling this tool.
    """
    try:
        payload: Dict[str, Any] = {
            "name": connection_name,
            "startDiscoveryJob": True,
            "cloudType": "aws",
            "connectionType": "OrganizationLevel",
            "cloudSpecificConfiguration": {
                "aws": {
                    "regions": "default",
                    "iamRoleAccountId": iam_role_account_id,
                    "organizationConfiguration": {
                        "content": {
                            "accounts": [],
                            "discoverAllAccounts": discover_all_accounts,
                        },
                        "enableOwnerDetection": False,
                    },
                }
            },
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
        logger.error(f"Error creating AWS protection group - invalid workloads_json: {e}")
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


_STACKSET_NAME = "CommvaultMemberAccountDiscovery"
_ACCESS_STACK_NAME = "CommvaultPermissionsStack"
_DEFAULT_TARGET_OU_ID = "ou-anxa-qikxlrp2"

# Heuristic substrings used to match parameter keys discovered from the CFT
_ROLE_ARN_HINTS = ("rolearn", "role_arn", "rolearn", "infrarolearn", "infrarole")
_USER_ARN_HINTS = ("userarn", "user_arn", "infrauserarn", "infrauser")
_EXTERNAL_ID_HINTS = ("externalid", "external_id", "extid")

# ---------------------------------------------------------------------------
# Initial access-role CFT helpers
# ---------------------------------------------------------------------------


def _match_param_key(keys: list[str], hints: tuple) -> str | None:
    """Return the first key whose lowercased name contains any of the hint substrings."""
    for key in keys:
        lower = key.lower().replace("-", "").replace("_", "")
        if any(h in lower for h in hints):
            return key
    return None


def deploy_commvault_access_cft(
    template_url: str,
    infra_role_arn: str,
    external_id: str = "",
    stack_name: str = _ACCESS_STACK_NAME,
    region: str = "us-east-1",
) -> dict:
    """Deploy the Commvault cross-account access role CloudFormation stack.

    This creates the IAM role in the delegated admin account that allows
    Commvault to access the AWS environment.  Idempotent: if the stack already
    exists and is CREATE_COMPLETE it returns immediately.  Uses boto3 with the
    current AWS CLI / environment credentials.

    Args:
        template_url: S3 URL of the CFT template from get_aws_permissions_cft.
        infra_role_arn: Commvault hosted-infra IAM role ARN (from CFT response).
        external_id: External ID value from get_aws_permissions_cft.
        stack_name: CloudFormation stack name. Defaults to "CommvaultPermissionsStack".
        region: AWS region to deploy into. Defaults to "us-east-1".
    """
    try:
        cfn = boto3.client("cloudformation", region_name=region)

        # Idempotency check
        try:
            desc = cfn.describe_stacks(StackName=stack_name)
            existing = desc["Stacks"][0]
            status = existing.get("StackStatus", "")
            if status == "CREATE_COMPLETE":
                return {
                    "alreadyDeployed": True,
                    "stackName": stack_name,
                    "status": status,
                    "message": "Access role stack is already deployed.",
                }
            if status in ("CREATE_IN_PROGRESS", "UPDATE_IN_PROGRESS"):
                return {
                    "inProgress": True,
                    "stackName": stack_name,
                    "status": status,
                    "message": "A deployment is already in progress.",
                }
            if status in (
                "ROLLBACK_COMPLETE", "CREATE_FAILED", "ROLLBACK_FAILED",
                "UPDATE_ROLLBACK_COMPLETE", "DELETE_FAILED",
            ):
                return {
                    "error": True,
                    "stackName": stack_name,
                    "status": status,
                    "message": (
                        f"Stack '{stack_name}' is in {status} state. "
                        "Delete it manually in the AWS Console before retrying."
                    ),
                }
        except ClientError as e:
            if e.response["Error"]["Code"] != "ValidationError":
                raise

        # Discover template parameter keys dynamically
        summary = cfn.get_template_summary(TemplateURL=template_url)
        param_keys = [p["ParameterKey"] for p in summary.get("Parameters", [])]

        params = []
        role_key = _match_param_key(param_keys, _ROLE_ARN_HINTS)
        external_id_key = _match_param_key(param_keys, _EXTERNAL_ID_HINTS)
        if role_key:
            params.append({"ParameterKey": role_key, "ParameterValue": infra_role_arn})
        if external_id_key:
            params.append({"ParameterKey": external_id_key, "ParameterValue": external_id})

        cfn.create_stack(
            StackName=stack_name,
            TemplateURL=template_url,
            Parameters=params,
            Capabilities=["CAPABILITY_NAMED_IAM"],
            OnFailure="ROLLBACK",
        )
        return {
            "success": True,
            "stackName": stack_name,
            "status": "CREATE_IN_PROGRESS",
            "message": (
                "Stack deployment started. "
                "Use get_commvault_access_cft_status to monitor progress."
            ),
        }
    except NoCredentialsError as e:
        logger.error(f"deploy_commvault_access_cft error - no AWS credentials: {e}")
        return {"error": "No AWS credentials found. Run 'aws configure' or set environment variables."}
    except ClientError as e:
        logger.error(f"deploy_commvault_access_cft error: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"deploy_commvault_access_cft error: {e}")
        return {"error": str(e)}


def get_commvault_access_cft_status(
    stack_name: str = _ACCESS_STACK_NAME,
    region: str = "us-east-1",
) -> dict:
    """Poll the status of the Commvault access role CloudFormation stack.

    Call repeatedly until ``status`` is ``CREATE_COMPLETE`` (success) or a
    ``*_FAILED`` / ``ROLLBACK_*`` state (failure).

    Args:
        stack_name: CloudFormation stack name. Defaults to "CommvaultCloudAccess".
        region: AWS region where the stack was deployed. Defaults to "us-east-1".
    """
    try:
        cfn = boto3.client("cloudformation", region_name=region)
        desc = cfn.describe_stacks(StackName=stack_name)
        stack = desc["Stacks"][0]
        status = stack.get("StackStatus", "UNKNOWN")
        reason = stack.get("StackStatusReason", "")

        terminal_ok = {"CREATE_COMPLETE", "UPDATE_COMPLETE"}
        terminal_fail = {
            "CREATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED",
            "DELETE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
        }

        result: dict = {"stackName": stack_name, "status": status}
        if status in terminal_ok:
            result["done"] = True
        elif status in terminal_fail:
            result["failed"] = True
            if reason:
                result["reason"] = reason
        return result
    except ClientError as e:
        if e.response["Error"]["Code"] == "ValidationError":
            logger.error(f"get_commvault_access_cft_status error - stack not found: {e}")
            return {"error": f"Stack '{stack_name}' not found."}
        logger.error(f"get_commvault_access_cft_status error: {e}")
        return {"error": str(e)}
    except NoCredentialsError as e:
        logger.error(f"get_commvault_access_cft_status error - no AWS credentials: {e}")
        return {"error": "No AWS credentials found. Run 'aws configure' or set environment variables."}
    except Exception as e:
        logger.error(f"get_commvault_access_cft_status error: {e}")
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# StackSet / Organizations helpers
# ---------------------------------------------------------------------------


def list_org_units(parent_id: str) -> dict:
    """List AWS Organizational Units that are direct children of a parent node.

    Args:
        parent_id: The parent ID to list OUs under. Use "root" to list the
            root-level OUs. The function automatically resolves the literal
            string "root" to the actual root ID via
            organizations.list_roots().  You can also pass a real parent ID
            such as "r-xxxx" (root) or "ou-xxxx-yyyyyyyy" (OU).
    """
    try:
        org = boto3.client("organizations")

        if parent_id.strip().lower() == "root":
            roots = org.list_roots().get("Roots", [])
            if not roots:
                return {"error": "No AWS Organization roots found."}
            parent_id = roots[0]["Id"]

        paginator = org.get_paginator("list_organizational_units_for_parent")
        ous = []
        for page in paginator.paginate(ParentId=parent_id):
            ous.extend(page.get("OrganizationalUnits", []))

        return filter_org_units_response({"OrganizationalUnits": ous})
    except NoCredentialsError as e:
        logger.error(f"list_org_units error - no AWS credentials: {e}")
        return {"error": "No AWS credentials found. Run 'aws configure' or set environment variables."}
    except ClientError as e:
        logger.error(f"list_org_units error: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"list_org_units error: {e}")
        return {"error": str(e)}


def check_member_stackset_status(
    target_ou_id: str = _DEFAULT_TARGET_OU_ID,
    call_as: str = "DELEGATED_ADMIN",
) -> dict:
    """Check whether the Commvault member-account StackSet is already deployed to an OU.

    Returns one of three shapes:
    - {"exists": False, "notDeployed": True}  – StackSet doesn't exist at all
    - {"alreadyDeployed": True, ...}           – all instances in the OU SUCCEEDED
    - {"status": "RUNNING"|"FAILED", ...}      – in-flight or needs attention

    Args:
        target_ou_id: The OU ID to check. Defaults to "ou-anxa-qikxlrp2".
        call_as: StackSets caller context. Defaults to "DELEGATED_ADMIN";
            use "SELF" only from the AWS Organization management account.
    """
    try:
        cfn = boto3.client("cloudformation")
        paginator = cfn.get_paginator("list_stack_instances")

        # Check if the StackSet itself exists
        try:
            cfn.describe_stack_set(StackSetName=_STACKSET_NAME, CallAs=call_as)
        except ClientError as e:
            if e.response["Error"]["Code"] == "StackSetNotFoundException":
                return filter_member_stackset_check(False, {}, target_ou_id)
            raise

        summaries = []
        for page in paginator.paginate(StackSetName=_STACKSET_NAME, CallAs=call_as):
            summaries.extend(page.get("Summaries", []))

        return filter_member_stackset_check(True, {"Summaries": summaries}, target_ou_id)
    except NoCredentialsError as e:
        logger.error(f"check_member_stackset_status error - no AWS credentials: {e}")
        return {"error": "No AWS credentials found. Run 'aws configure' or set environment variables."}
    except ClientError as e:
        logger.error(f"check_member_stackset_status error: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"check_member_stackset_status error: {e}")
        return {"error": str(e)}


def deploy_member_account_stackset(
    template_url: str,
    infra_role_arn: str,
    infra_user_arn: str,
    target_ou_id: str = _DEFAULT_TARGET_OU_ID,
    deployment_region: str = "us-east-1",
    permission_model: str = "SERVICE_MANAGED",
    call_as: str = "DELEGATED_ADMIN",
    poll_interval_seconds: int = 15,
    max_wait_seconds: int = 1800,
) -> dict:
    """Create (or update) and deploy the Commvault member-account discovery StackSet.

    This is idempotent: if the StackSet already exists it is updated in-place;
    if it does not exist it is created.  Stack instances are then deployed to
    every account inside `target_ou_id`.

    Args:
        template_url: S3 URL to the CloudFormation template, from
            get_aws_permissions_cft connectionTypes.organization.memberAccountSetup.templateUrl.
        infra_role_arn: Commvault hosted-infra IAM role ARN, from
            get_aws_permissions_cft connectionTypes.organization.memberAccountSetup.hostedInfraRoleArn.
        infra_user_arn: Commvault hosted-infra IAM user ARN, from
            get_aws_permissions_cft connectionTypes.organization.memberAccountSetup.hostedInfraUserArn.
        target_ou_id: OU ID whose member accounts should receive the stack
            instance. Defaults to "ou-anxa-qikxlrp2".
        deployment_region: AWS region in which instances are deployed.
            Defaults to "us-east-1".
        permission_model: StackSet permission model. Defaults to "SERVICE_MANAGED",
            which is required for OU-based targeting. The caller must run from
            an AWS Organization management account or delegated StackSets
            administrator.
        call_as: StackSets caller context. Defaults to "DELEGATED_ADMIN";
            use "SELF" only from the AWS Organization management account.
        poll_interval_seconds: Seconds to wait between deployment status checks.
            Defaults to 15.
        max_wait_seconds: Maximum seconds to wait for the StackSet operation to
            reach a terminal state. Defaults to 1800 (30 minutes).
    """
    try:
        cfn = boto3.client("cloudformation")

        # Discover the parameter keys expected by the template
        template_summary = cfn.get_template_summary(TemplateURL=template_url)
        param_keys = [p["ParameterKey"] for p in template_summary.get("Parameters", [])]

        params = []
        role_key = _match_param_key(param_keys, _ROLE_ARN_HINTS)
        user_key = _match_param_key(param_keys, _USER_ARN_HINTS)
        if role_key:
            params.append({"ParameterKey": role_key, "ParameterValue": infra_role_arn})
        if user_key:
            params.append({"ParameterKey": user_key, "ParameterValue": infra_user_arn})

        capabilities = ["CAPABILITY_NAMED_IAM"]
        stackset_kwargs: Dict[str, Any] = {
            "TemplateURL": template_url,
            "Parameters": params,
            "Capabilities": capabilities,
            "PermissionModel": permission_model,
        }
        if permission_model == "SERVICE_MANAGED":
            stackset_kwargs["AutoDeployment"] = {
                "Enabled": True,
                "RetainStacksOnAccountRemoval": False,
            }

        # Create or update the StackSet definition. AWS does not allow changing
        # a StackSet's permission model after creation.
        existing_perm_model = None
        try:
            desc = cfn.describe_stack_set(StackSetName=_STACKSET_NAME, CallAs=call_as)
            existing_perm_model = desc["StackSet"].get("PermissionModel")
        except ClientError as e:
            if e.response["Error"]["Code"] == "StackSetNotFoundException":
                existing_perm_model = None
            else:
                raise

        if existing_perm_model and existing_perm_model != permission_model:
            return {
                "error": True,
                "stackSetName": _STACKSET_NAME,
                "existingPermissionModel": existing_perm_model,
                "requestedPermissionModel": permission_model,
                "message": (
                    f"StackSet '{_STACKSET_NAME}' exists with PermissionModel="
                    f"{existing_perm_model}, but {permission_model} was requested. "
                    "AWS does not allow changing the permission model. "
                    "Delete the existing StackSet manually in the AWS Console and retry."
                ),
            }

        stackset_exists = existing_perm_model is not None
        if stackset_exists:
            try:
                cfn.update_stack_set(
                    StackSetName=_STACKSET_NAME,
                    CallAs=call_as,
                    **stackset_kwargs,
                )
            except ClientError as e:
                if e.response["Error"]["Code"] != "StackSetNotEmptyException":
                    raise
        else:
            cfn.create_stack_set(
                StackSetName=_STACKSET_NAME,
                Description="Commvault member-account discovery role",
                CallAs=call_as,
                **stackset_kwargs,
            )

        # Deploy instances to the target OU
        response = cfn.create_stack_instances(
            StackSetName=_STACKSET_NAME,
            CallAs=call_as,
            DeploymentTargets={"OrganizationalUnitIds": [target_ou_id]},
            Regions=[deployment_region],
            OperationPreferences={
                "RegionConcurrencyType": "PARALLEL",
                "FailureTolerancePercentage": 100,
                "MaxConcurrentPercentage": 100,
            },
        )
        operation_id = response.get("OperationId")

        terminal_statuses = {"SUCCEEDED", "FAILED", "STOPPED"}
        deadline = time.time() + max_wait_seconds
        final_status_payload: dict[str, Any] = {}

        while True:
            op_response = cfn.describe_stack_set_operation(
                StackSetName=_STACKSET_NAME,
                OperationId=operation_id,
                CallAs=call_as,
            )

            paginator = cfn.get_paginator("list_stack_instances")
            summaries = []
            for page in paginator.paginate(StackSetName=_STACKSET_NAME, CallAs=call_as):
                summaries.extend(page.get("Summaries", []))

            final_status_payload = filter_stackset_status_response(
                op_response,
                {"Summaries": summaries},
            )
            status = final_status_payload.get("status")

            if status in terminal_statuses:
                break

            if time.time() >= deadline:
                final_status_payload["timedOut"] = True
                break

            time.sleep(poll_interval_seconds)

        timed_out = final_status_payload.get("timedOut") is True
        status = final_status_payload.get("status")

        return {
            "success": status == "SUCCEEDED",
            "stackSetName": _STACKSET_NAME,
            "operationId": operation_id,
            "targetOuId": target_ou_id,
            "region": deployment_region,
            "finalStatus": final_status_payload,
            "message": (
                f"StackSet deployment finished with status={status}."
                if not timed_out
                else (
                    f"StackSet deployment did not finish within {max_wait_seconds}s; "
                    "call get_stackset_deployment_status with the operationId to keep polling."
                )
            ),
        }
    except NoCredentialsError as e:
        logger.error(f"deploy_member_account_stackset error - no AWS credentials: {e}")
        return {"error": "No AWS credentials found. Run 'aws configure' or set environment variables."}
    except ClientError as e:
        logger.error(f"deploy_member_account_stackset error: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"deploy_member_account_stackset error: {e}")
        return {"error": str(e)}


def get_stackset_deployment_status(
    operation_id: str,
    call_as: str = "DELEGATED_ADMIN",
) -> dict:
    """Poll the status of a running StackSet deployment operation.

    Call repeatedly until ``status`` is one of SUCCEEDED / FAILED / STOPPED.

    Args:
        operation_id: The operationId returned by deploy_member_account_stackset.
        call_as: StackSets caller context. Defaults to "DELEGATED_ADMIN";
            use "SELF" only from the AWS Organization management account.
    """
    try:
        cfn = boto3.client("cloudformation")

        op_response = cfn.describe_stack_set_operation(
            StackSetName=_STACKSET_NAME,
            OperationId=operation_id,
            CallAs=call_as,
        )

        paginator = cfn.get_paginator("list_stack_instances")
        summaries = []
        for page in paginator.paginate(StackSetName=_STACKSET_NAME, CallAs=call_as):
            summaries.extend(page.get("Summaries", []))

        return filter_stackset_status_response(
            op_response,
            {"Summaries": summaries},
        )
    except NoCredentialsError as e:
        logger.error(f"get_stackset_deployment_status error - no AWS credentials: {e}")
        return {"error": "No AWS credentials found. Run 'aws configure' or set environment variables."}
    except ClientError as e:
        logger.error(f"get_stackset_deployment_status error: {e}")
        return {"error": str(e)}
    except Exception as e:
        logger.error(f"get_stackset_deployment_status error: {e}")
        return {"error": str(e)}


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
    deploy_commvault_access_cft,
    get_commvault_access_cft_status,
    validate_aws_cloud_credentials,
    browse_aws_cloud_accounts,
    create_aws_cloud_connection,
    list_aws_cloud_connections,
    list_aws_workloads,
    list_eligible_plans,
    create_aws_protection_group,
    start_aws_protection_group_backup,
    list_org_units,
    check_member_stackset_status,
    deploy_member_account_stackset,
    get_stackset_deployment_status,
]
