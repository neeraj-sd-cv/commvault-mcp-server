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

import requests

from src.cv_api_client import commvault_api_client
from src.logger import logger
from src.utils import get_env_var

from src.wrappers import (
    filter_aws_permissions_cft_response,
    filter_aws_cloud_connections_response,
    filter_aws_solutions_response,
    filter_eligible_plans_response,
    parse_cft_quick_create_params,
)


_STACKSET_NAME = "CommvaultMemberAccountDiscovery"
_DEFAULT_TARGET_OU_ID = "ou-anxa-qikxlrp2"


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
    """Fetch the CloudFormation quick-create links and IAM role details for AWS onboarding.

    Call this first, then:
    1. Pass ``connectionTypes.organization`` values to ``get_access_role_setup_steps``
       and follow those steps until the stack is CREATE_COMPLETE.
    2. Call ``validate_aws_cloud_credentials`` to confirm Commvault can assume the role.
    3. Pass ``connectionTypes.organization.memberAccountSetup`` values to
       ``get_member_discovery_setup_steps`` and follow those steps until SUCCEEDED.
    4. Call ``browse_aws_cloud_accounts`` to confirm account discovery.
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
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else None
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text if e.response is not None else str(e)
        logger.error(f"HTTP {status_code} error starting AWS protection group backup: {body}")
        return {
            "error": True,
            "httpStatus": status_code,
            "message": f"Failed to start AWS protection group backup: HTTP {status_code}",
            "apiResponse": body,
        }
    except Exception as e:
        logger.error(f"Error starting AWS protection group backup: {e}")
        return {
            "error": True,
            "message": f"Failed to start AWS protection group backup: {str(e)}",
        }


# ---------------------------------------------------------------------------
# Stateless step-emitting tools (no AWS credentials required)
# ---------------------------------------------------------------------------

_ACCESS_STACK_NAME = "CommvaultPermissionsStack"


def get_access_role_setup_steps(
    quick_create_url: Annotated[
        str,
        Field(
            description=(
                "The CFT quick-create URL from get_aws_permissions_cft "
                "(connectionTypes.organization.cftQuickCreateUrl)."
            )
        ),
    ],
    infra_role_arn: Annotated[
        str,
        Field(
            description=(
                "The Commvault hosted-infra IAM role ARN from get_aws_permissions_cft "
                "(connectionTypes.organization.iamRoleArn)."
            )
        ),
    ],
    external_id: Annotated[
        str,
        Field(
            description=(
                "The external ID from get_aws_permissions_cft "
                "(connectionTypes.organization.externalId)."
            )
        ),
    ] = "",
    region: Annotated[str, Field(description="AWS region. Defaults to us-east-1.")] = "us-east-1",
    stack_name: Annotated[
        str,
        Field(description="CloudFormation stack name. Defaults to CommvaultPermissionsStack."),
    ] = _ACCESS_STACK_NAME,
) -> dict:
    """Return the steps needed to deploy the Commvault cross-account access-role CFT.

    Returns a ``browserUrl`` the user can open in the AWS Console to create the stack
    with one click, plus ``cliCommands`` the agent can run locally: a ``create-stack``
    command and a ``describe-stacks`` status-check command.

    Call ``validate_aws_cloud_credentials`` once the stack reaches CREATE_COMPLETE.
    """
    parsed = parse_cft_quick_create_params(quick_create_url)
    template_url = parsed["templateUrl"] or quick_create_url

    # Build --parameters string for the CLI command
    params_parts = []
    for p in parsed["params"]:
        params_parts.append(f"ParameterKey={p['ParameterKey']},ParameterValue=\"{p['ParameterValue']}\"")
    # Ensure the infra_role_arn and external_id are represented even if the
    # quick-create URL did not embed them as param_ values
    param_keys_present = {p["ParameterKey"].lower() for p in parsed["params"]}
    if infra_role_arn and not any("role" in k for k in param_keys_present):
        params_parts.append(f"ParameterKey=HostedInfraRoleArn,ParameterValue=\"{infra_role_arn}\"")
    if external_id and not any("external" in k or "extid" in k for k in param_keys_present):
        params_parts.append(f"ParameterKey=ExternalId,ParameterValue=\"{external_id}\"")

    params_flag = ""
    if params_parts:
        params_flag = " \\\n  --parameters " + " \\\n              ".join(params_parts)

    create_cmd = (
        f"aws cloudformation create-stack \\\n"
        f"  --stack-name {stack_name} \\\n"
        f"  --template-url \"{template_url}\" \\\n"
        f"  --capabilities CAPABILITY_NAMED_IAM \\\n"
        f"  --on-failure ROLLBACK \\\n"
        f"  --region {region}"
        f"{params_flag}"
    )

    status_cmd = (
        f"aws cloudformation describe-stacks \\\n"
        f"  --stack-name {stack_name} \\\n"
        f"  --region {region} \\\n"
        f"  --query \"Stacks[0].StackStatus\" \\\n"
        f"  --output text"
    )

    return {
        "browserUrl": quick_create_url,
        "browserInstructions": (
            "Open the URL above in your AWS Console (make sure you are signed into the "
            "delegated admin account). Review the pre-filled parameters and click "
            "'Create stack'. Wait for the stack status to show CREATE_COMPLETE."
        ),
        "cliCommands": [
            {
                "description": "Create the Commvault access-role stack",
                "command": create_cmd,
            },
            {
                "description": "Poll stack status (run until CREATE_COMPLETE)",
                "command": status_cmd,
            },
        ],
        "stackName": stack_name,
        "region": region,
        "notes": (
            "The stack creates an IAM role that allows Commvault to access your AWS account. "
            "It typically takes 1–2 minutes to reach CREATE_COMPLETE. "
            "Once complete, call validate_aws_cloud_credentials to confirm connectivity."
        ),
    }


def get_member_discovery_setup_steps(
    template_url: Annotated[
        str,
        Field(
            description=(
                "StackSet template S3 URL from get_aws_permissions_cft "
                "(connectionTypes.organization.memberAccountSetup.templateUrl)."
            )
        ),
    ],
    infra_role_arn: Annotated[
        str,
        Field(
            description=(
                "Commvault hosted-infra IAM role ARN from get_aws_permissions_cft "
                "(connectionTypes.organization.memberAccountSetup.hostedInfraRoleArn)."
            )
        ),
    ],
    infra_user_arn: Annotated[
        str,
        Field(
            description=(
                "Commvault hosted-infra IAM user ARN from get_aws_permissions_cft "
                "(connectionTypes.organization.memberAccountSetup.hostedInfraUserArn)."
            )
        ),
    ],
    target_ou_id: Annotated[
        str,
        Field(description="Target OU ID. Defaults to the standard Commvault default OU."),
    ] = _DEFAULT_TARGET_OU_ID,
    region: Annotated[
        str,
        Field(description="AWS region for stack instances. Defaults to us-east-1."),
    ] = "us-east-1",
) -> dict:
    """Return the steps needed to deploy the CommvaultMemberAccountDiscovery StackSet.

    Returns ``browserSteps`` the user can follow in the AWS Console, plus
    ``cliCommands`` the agent can run locally: create the StackSet definition,
    deploy instances to the target OU, and poll status.

    Call ``browse_aws_cloud_accounts`` once all instances reach SUCCEEDED.
    """
    stackset_name = _STACKSET_NAME

    discover_params_cmd = (
        f"aws cloudformation get-template-summary \\\n"
        f"  --template-url \"{template_url}\" \\\n"
        f"  --query \"Parameters[].ParameterKey\" \\\n"
        f"  --output text"
    )

    create_stackset_cmd = (
        f"# Run cliCommands[0] first to confirm the actual parameter key names in this template.\n"
        f"aws cloudformation create-stack-set \\\n"
        f"  --stack-set-name {stackset_name} \\\n"
        f"  --template-url \"{template_url}\" \\\n"
        f"  --parameters "
        f"ParameterKey=HostedInfraRoleArn,ParameterValue=\"{infra_role_arn}\" "
        f"ParameterKey=HostedInfraUserArn,ParameterValue=\"{infra_user_arn}\" \\\n"
        f"  --capabilities CAPABILITY_NAMED_IAM \\\n"
        f"  --permission-model SERVICE_MANAGED \\\n"
        f"  --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false \\\n"
        f"  --call-as DELEGATED_ADMIN"
    )

    deploy_instances_cmd = (
        f"aws cloudformation create-stack-instances \\\n"
        f"  --stack-set-name {stackset_name} \\\n"
        f"  --deployment-targets OrganizationalUnitIds={target_ou_id} \\\n"
        f"  --regions {region} \\\n"
        f"  --operation-preferences "
        f"RegionConcurrencyType=PARALLEL,FailureTolerancePercentage=100,MaxConcurrentPercentage=100 \\\n"
        f"  --call-as DELEGATED_ADMIN"
    )

    status_cmd = (
        f"aws cloudformation list-stack-instances \\\n"
        f"  --stack-set-name {stackset_name} \\\n"
        f"  --call-as DELEGATED_ADMIN \\\n"
        f"  --no-paginate \\\n"
        f"  --query \"Summaries[?OrganizationalUnitId=='{target_ou_id}']"
        f".[Account,StackInstanceStatus.DetailedStatus]\" \\\n"
        f"  --output table"
    )

    discover_ou_cmd = (
        "# Optional: list root OUs to find the correct target_ou_id\n"
        "aws organizations list-roots --query \"Roots[0].Id\" --output text\n"
        "# Then list child OUs (replace ROOT_ID with output above):\n"
        "aws organizations list-organizational-units-for-parent \\\n"
        "  --parent-id ROOT_ID \\\n"
        "  --query \"OrganizationalUnits[].{Id:Id,Name:Name}\" \\\n"
        "  --output table"
    )

    browser_steps = (
        "Deploy CommvaultMemberAccountDiscovery StackSet via AWS Console:\n\n"
        "1. Sign in to the delegated admin account and go to:\n"
        "   https://console.aws.amazon.com/cloudformation/home#/stacksets/create\n"
        "2. Under 'Permissions', select 'Service-managed permissions'.\n"
        "3. Select 'Specify template' → 'Amazon S3 URL' and paste:\n"
        f"   {template_url}\n"
        "4. Click 'Next'. Under Parameters, enter:\n"
        f"   - HostedInfraRoleArn: {infra_role_arn}\n"
        f"   - HostedInfraUserArn: {infra_user_arn}\n"
        "5. Click 'Next' through stack options. Under 'Deployment targets',\n"
        "   select 'Deploy to organizational units (OUs)' and paste:\n"
        f"   {target_ou_id}\n"
        f"6. Select region: {region}. Click 'Next' then 'Submit'.\n"
        "7. Monitor the StackSet operation until status shows SUCCEEDED."
    )

    return {
        "browserSteps": browser_steps,
        "cliCommands": [
            {
                "description": "(Optional) Inspect template parameter keys",
                "command": discover_params_cmd,
            },
            {
                "description": "Create the StackSet definition",
                "command": create_stackset_cmd,
            },
            {
                "description": "Deploy stack instances to the target OU",
                "command": deploy_instances_cmd,
            },
            {
                "description": "Check deployment status (run until all show SUCCEEDED)",
                "command": status_cmd,
            },
            {
                "description": "(Optional) Discover OU IDs if you need a different target OU",
                "command": discover_ou_cmd,
            },
        ],
        "stackSetName": stackset_name,
        "targetOuId": target_ou_id,
        "region": region,
        "notes": (
            "The StackSet deploys an IAM role into every member account in the target OU, "
            "allowing Commvault to discover and protect resources across those accounts. "
            "Deployment time depends on the number of member accounts (typically 1–5 minutes). "
            "Once all instances show SUCCEEDED, call browse_aws_cloud_accounts to verify discovery."
        ),
    }


_SKILL_FILE = Path(__file__).parent.parent.parent / ".claude" / "skills" / "aws-onboarding" / "SKILL.md"


def get_aws_onboarding_instructions() -> dict:
    """Load the complete AWS onboarding and cloud protection workflow guide.

    Call this tool FIRST whenever the user wants to do ANYTHING with AWS in Commvault,
    including but not limited to:
    - Onboarding an AWS account or AWS Organization
    - Connecting AWS to Commvault (cloud connection setup)
    - Setting up CFT (CloudFormation Template) permissions or IAM roles
    - Protecting or backing up AWS workloads: EC2, S3, RDS, EBS, EFS, DynamoDB
    - Creating an AWS protection group or backup plan
    - Running or scheduling a backup job for AWS resources
    - Validating AWS credentials or IAM role access
    - Discovering AWS member accounts via StackSet

    Returns the full step-by-step guided workflow covering every stage from
    CFT deployment through protection group creation and initial backup.
    """
    try:
        instructions = _SKILL_FILE.read_text(encoding="utf-8")
        return {"instructions": instructions}
    except Exception as e:
        logger.error(f"Error reading AWS onboarding instructions: {e}")
        raise ToolError(f"Failed to load AWS onboarding instructions: {str(e)}")


def load_aws_backup_protection_guide() -> dict:
    """Load the step-by-step guide for protecting and backing up AWS cloud workloads with Commvault.

    Use this tool when the user asks to:
    - Back up EC2 instances, S3 buckets, RDS databases, EBS volumes, EFS, or DynamoDB
    - Set up cloud backup for any AWS service
    - Protect AWS workloads or create a cloud protection group
    - Schedule or run backup jobs targeting AWS resources
    - Add Commvault cloud protection to an existing AWS environment
    - Restore AWS workloads from a Commvault backup
    - Connect an AWS account as a Commvault cloud source

    Returns the full guided workflow (identical to get_aws_onboarding_instructions)
    covering every step from initial AWS permissions setup through backup execution.
    """
    try:
        instructions = _SKILL_FILE.read_text(encoding="utf-8")
        return {"instructions": instructions}
    except Exception as e:
        logger.error(f"Error reading AWS backup protection guide: {e}")
        raise ToolError(f"Failed to load AWS backup protection guide: {str(e)}")


def cleanup_aws_demo_environment(
    target_ou_id: Annotated[
        str,
        Field(description="Target OU ID whose StackSet instances will be deleted. Defaults to the standard Commvault demo OU."),
    ] = _DEFAULT_TARGET_OU_ID,
    region: Annotated[
        str,
        Field(description="AWS region for all operations. Defaults to us-east-1."),
    ] = "us-east-1",
    call_as: Annotated[
        str,
        Field(description="CloudFormation call-as value for StackSet operations. Defaults to DELEGATED_ADMIN."),
    ] = "DELEGATED_ADMIN",
) -> dict:
    """Return ordered AWS CLI commands to tear down the Commvault AWS demo environment.

    Use this tool when the user asks to:
    - clean up AWS, clean the setup, clean demo, tear down AWS, reset the AWS demo
    - undo AWS onboarding, delete the Commvault CloudFormation stack or StackSet
    - remove CommvaultPermissionsStack or CommvaultMemberAccountDiscovery
    - start fresh, re-run onboarding from scratch, wipe the AWS setup

    DESTRUCTIVE — the returned commands permanently delete:
    - CloudFormation stack   : CommvaultPermissionsStack
    - StackSet instances     : CommvaultMemberAccountDiscovery (in the target OU)
    - StackSet definition    : CommvaultMemberAccountDiscovery

    IMPORTANT — before executing any CLI commands, the agent MUST:
      1. Call ``list_aws_cloud_connections`` and offer to delete each existing AWS
         cloud connection by calling ``delete_aws_cloud_connection``. Deleting the
         connection also removes its associated protection group. Ask the user to
         confirm before deleting. Only skip this step if the user explicitly says
         they do not want to delete the connection.
      2. Show all CLI commands from ``cliCommands`` to the user and obtain explicit
         confirmation before running any of them.

    Execute the three CLI stages in order after confirmation:
      1. Run stage 1 commands to delete and wait for the access-role stack.
      2. Run stage 2 commands: delete-stack-instances, then poll
         describe-stack-set-operation with the OperationId parsed from the
         delete-stack-instances JSON output until status is SUCCEEDED (or FAILED).
         Then verify zero instances remain.
      3. Only if stage 2 succeeded with zero instances, run stage 3 to delete the
         StackSet definition.
    """
    stack_name = _ACCESS_STACK_NAME
    stackset_name = _STACKSET_NAME

    delete_stack_cmd = (
        f"aws cloudformation delete-stack \\\n"
        f"  --stack-name {stack_name} \\\n"
        f"  --region {region}"
    )

    wait_stack_cmd = (
        f"aws cloudformation wait stack-delete-complete \\\n"
        f"  --stack-name {stack_name} \\\n"
        f"  --region {region}"
    )

    delete_instances_cmd = (
        f"aws cloudformation delete-stack-instances \\\n"
        f"  --stack-set-name {stackset_name} \\\n"
        f"  --deployment-targets OrganizationalUnitIds={target_ou_id} \\\n"
        f"  --regions {region} \\\n"
        f"  --no-retain-stacks \\\n"
        f"  --call-as {call_as} \\\n"
        f"  --region {region} \\\n"
        f"  --output json"
    )

    poll_operation_cmd = (
        f"# Replace <OPERATION_ID> with the OperationId value from the delete-stack-instances output above\n"
        f"aws cloudformation describe-stack-set-operation \\\n"
        f"  --stack-set-name {stackset_name} \\\n"
        f"  --operation-id <OPERATION_ID> \\\n"
        f"  --call-as {call_as} \\\n"
        f"  --region {region} \\\n"
        f"  --query \"StackSetOperation.Status\" \\\n"
        f"  --output text"
    )

    verify_instances_cmd = (
        f"aws cloudformation list-stack-instances \\\n"
        f"  --stack-set-name {stackset_name} \\\n"
        f"  --call-as {call_as} \\\n"
        f"  --region {region} \\\n"
        f"  --query \"length(Summaries)\" \\\n"
        f"  --output text"
    )

    delete_stackset_cmd = (
        f"aws cloudformation delete-stack-set \\\n"
        f"  --stack-set-name {stackset_name} \\\n"
        f"  --call-as {call_as} \\\n"
        f"  --region {region}"
    )

    return {
        "stackName": stack_name,
        "stackSetName": stackset_name,
        "targetOuId": target_ou_id,
        "region": region,
        "callAs": call_as,
        "preCleanupSteps": [
            {
                "step": 1,
                "action": "delete_commvault_connection",
                "description": (
                    "BEFORE running any CLI commands: call list_aws_cloud_connections, "
                    "then offer to delete each AWS cloud connection by calling "
                    "delete_aws_cloud_connection (confirm with the user first). "
                    "Deleting the connection also removes its associated protection group. "
                    "Skip only if the user explicitly declines."
                ),
                "toolsToCall": ["list_aws_cloud_connections", "delete_aws_cloud_connection"],
            }
        ],
        "cliCommands": [
            {
                "stage": 1,
                "description": "Delete the Commvault access-role CloudFormation stack",
                "commands": [
                    {"description": "Delete the access-role stack", "command": delete_stack_cmd},
                    {"description": "Wait for stack deletion to complete", "command": wait_stack_cmd},
                ],
            },
            {
                "stage": 2,
                "description": "Delete CommvaultMemberAccountDiscovery StackSet instances from the OU",
                "commands": [
                    {
                        "description": "Delete stack instances (capture OperationId from JSON output)",
                        "command": delete_instances_cmd,
                    },
                    {
                        "description": "Poll operation status every 15 s until SUCCEEDED or FAILED",
                        "command": poll_operation_cmd,
                    },
                    {
                        "description": "Verify zero StackSet instances remain (must return 0)",
                        "command": verify_instances_cmd,
                    },
                ],
            },
            {
                "stage": 3,
                "description": "Delete the StackSet definition (only after stage 2 shows zero instances)",
                "commands": [
                    {"description": "Delete the StackSet definition", "command": delete_stackset_cmd},
                ],
            },
        ],
        "notes": (
            "DESTRUCTIVE: these commands permanently delete AWS resources. "
            "Show all commands to the user and obtain explicit confirmation before running any of them. "
            "Execute stages in order. For stage 2, parse the OperationId from the "
            "delete-stack-instances JSON response and substitute it into the poll command. "
            "Only proceed to stage 3 after stage 2 reports zero remaining instances."
        ),
    }


def delete_aws_cloud_connection(
    connection_id: Annotated[
        int,
        Field(
            description=(
                "The numeric ID of the AWS cloud connection to delete "
                "(from list_aws_cloud_connections)."
            )
        ),
    ],
    connection_name: Annotated[
        str,
        Field(description="Display name of the connection, used for status messages."),
    ] = "",
) -> dict:
    """Retire and permanently delete an AWS cloud connection from Commvault.

    Retiring the cloud connection also deletes its associated protection group
    and stops all backup activity for that connection.

    Call this as part of the cleanup / teardown flow — before running the AWS
    CLI commands from ``cleanup_aws_demo_environment`` — to remove the Commvault
    side of the demo setup.

    Use ``list_aws_cloud_connections`` first to look up the ``connection_id``
    if it is not already known.
    """
    try:
        response = commvault_api_client.delete(f"Client/{connection_id}/Retire")
        display = connection_name or str(connection_id)
        return {
            "summary": {
                "connectionId": connection_id,
                "connectionName": display,
                "status": "deleted",
                "message": f"Cloud connection '{display}' has been retired and deleted.",
            },
            "apiResponse": response,
        }
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else None
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text if e.response is not None else str(e)
        logger.error(f"HTTP {status_code} error deleting AWS cloud connection {connection_id}: {body}")
        return {
            "error": True,
            "httpStatus": status_code,
            "message": f"Failed to delete cloud connection: HTTP {status_code}",
            "apiResponse": body,
        }
    except Exception as e:
        logger.error(f"Error deleting AWS cloud connection {connection_id}: {e}")
        return {
            "error": True,
            "message": f"Failed to delete cloud connection: {str(e)}",
        }


AWS_CLOUD_TOOLS = [
    get_aws_onboarding_instructions,
    load_aws_backup_protection_guide,
    get_aws_permissions_cft,
    get_access_role_setup_steps,
    get_member_discovery_setup_steps,
    validate_aws_cloud_credentials,
    browse_aws_cloud_accounts,
    create_aws_cloud_connection,
    list_aws_cloud_connections,
    list_aws_workloads,
    list_eligible_plans,
    create_aws_protection_group,
    start_aws_protection_group_backup,
    delete_aws_cloud_connection,
    cleanup_aws_demo_environment,
]
