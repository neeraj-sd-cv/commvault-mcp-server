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

from src.cv_api_client import commvault_api_client
from src.logger import logger
from src.wrappers import filter_aws_permissions_cft_response


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
        return response
    except ValueError as e:
        return {"error": True, "message": str(e)}
    except Exception as e:
        logger.error(f"Error creating AWS cloud connection: {e}")
        return {"error": True, "message": f"Failed to create AWS cloud connection: {str(e)}"}


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
]
