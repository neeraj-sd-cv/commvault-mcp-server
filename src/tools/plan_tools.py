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

import re

from fastmcp.exceptions import ToolError
from typing import Annotated
from pydantic import Field

from src.cv_api_client import commvault_api_client
from src.logger import logger
from src.wrappers import (
    filter_plan_storage_pool_list,
    filter_create_storage_pool_response,
    filter_create_server_plan_response,
)


# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

def sanitize_name_for_commvault(name: str) -> str:
    """
    Convert an arbitrary string to a Commvault-safe entity name:
    lowercase, spaces/special chars replaced with hyphens, max 50 chars,
    no leading/trailing hyphens, no consecutive hyphens.
    """
    lower = name.lower()
    safe = re.sub(r"[^a-z0-9]+", "-", lower)
    safe = safe.strip("-")
    safe = re.sub(r"-{2,}", "-", safe)
    return safe[:50]


def suggest_names(connection_name: str) -> dict:
    """
    Derive default storage, plan, and protection-group names from a
    cloud connection name.  Returns a dict with keys storage, plan,
    protection_group.
    """
    base = sanitize_name_for_commvault(connection_name)
    return {
        "storage": f"{base}-storage",
        "plan": f"{base}-plan",
        "protection_group": f"{base}-protection-group",
    }


# ---------------------------------------------------------------------------
# Existing read-only tools
# ---------------------------------------------------------------------------

def get_plan_list(only_s3_vault_compatible: Annotated[bool, Field(description="Whether to filter plans for S3 vault.")]) -> dict:
    """
    Gets the list of all plans. 
    Special Condition: While fetching plans list for S3 vault, set the `only_s3_vault_compatible` parameter to True.
    """
    try:
        if only_s3_vault_compatible:
            response = commvault_api_client.get("v2/plan?subType=Server&fq=plans.storage.copy.storagePoolType:in:1,4&fl=plans.plan.planId,plans.plan.planName")
        else:
            response = commvault_api_client.get("v4/plan/summary")
        return response
    except Exception as e:
        logger.error(f"Error retrieving plan list: {e}")
        return ToolError({"error": str(e)})
    
def get_plan_properties(plan_id: Annotated[str, Field(description="The plan id to retrieve properties for.")]) -> dict:
    """
    Gets properties for a given plan id.
    """
    try:
        return commvault_api_client.get(f"v4/plan/summary/{plan_id}")
    except Exception as e:
        logger.error(f"Error retrieving plan properties: {e}")
        return ToolError({"error": str(e)})
    
# ---------------------------------------------------------------------------
# Plan-creation sequence tools
# ---------------------------------------------------------------------------

# Default region used when the caller does not specify one.
_DEFAULT_REGION = {"name": "us-east-1", "displayName": "US East (N. Virginia)", "id": 50}


def create_storage_pool_for_plan(
    storage_name: Annotated[str, Field(description="Name for the new storage pool, e.g. 'my-connection-storage'.")],
    company_id: Annotated[int, Field(description="Commvault company ID associated with the cloud connection.")],
    company_name: Annotated[str, Field(description="Commvault company name associated with the cloud connection.")],
    region_name: Annotated[str, Field(description="AWS region name, e.g. 'us-east-1'. Defaults to 'us-east-1'.")] = _DEFAULT_REGION["name"],
    region_display_name: Annotated[str, Field(description="Human-readable region name, e.g. 'US East (N. Virginia)'.")] = _DEFAULT_REGION["displayName"],
    region_id: Annotated[int, Field(description="Commvault region ID. Default 50 corresponds to us-east-1.")] = _DEFAULT_REGION["id"],
) -> dict:
    """Create a Commvault storage pool to back a new server plan.

    This is **step 1** of the plan-creation subflow within AWS onboarding.

    Call this after the user confirms the suggested storage name.  Carry the
    returned ``storagePolicyId`` and ``storagePolicyName`` forward into
    ``create_server_plan``.

    Returns a compact summary with success flag, storagePolicyId, storagePolicyName,
    copyId, and copyName.  On failure, returns error details.
    """
    payload = {
        "storagePolicyName": storage_name,
        "copyName": "Primary",
        "type": "CVA_REGULAR_SP",
        "numberOfCopies": 1,
        "storagePolicyCopyInfo": {
            "copyType": "SYNCHRONOUS",
            "isDefault": "SET_TRUE",
            "active": "SET_TRUE",
            "storagePolicyFlags": {
                "blockLevelDedup": "SET_TRUE",
                "enableGlobalDeduplication": "SET_TRUE",
            },
            "library": {"libraryId": 0},
            "mediaAgent": {"mediaAgentName": "Automatic"},
            "retentionRules": {
                "retentionFlags": {"enableDataAging": "SET_TRUE"},
                "retainBackupDataForDays": -1,
                "retainBackupDataForCycles": -1,
                "retainArchiverDataForDays": -1,
            },
            "isFromGui": True,
            "numberOfStreamsToCombine": 1,
            "dedupeFlags": {
                "enableDeduplication": "SET_TRUE",
                "enableDASHFull": "SET_TRUE",
                "hostGlobalDedupStore": "SET_TRUE",
            },
            "DDBPartitionInfo": {
                "maInfoList": [
                    {
                        "mediaAgent": {"mediaAgentId": 0, "mediaAgentName": "Automatic"},
                        "subStoreList": [
                            {
                                "diskFreeThresholdMB": 5120,
                                "diskFreeWarningThreshholdMB": 10240,
                            }
                        ],
                    }
                ],
                "sidbStoreInfo": {"numSIDBStore": 2},
            },
        },
        "clientGroup": {"clientGroupId": 0},
        "storage": [
            {
                "mediaAgent": {"mediaAgentName": "Automatic"},
                "savedCredential": {"credentialId": 0},
                "deviceType": 400,
                "metallicStorageInfo": {
                    "region": [
                        {
                            "regionName": region_name,
                            "displayName": region_display_name,
                            "regionId": region_id,
                        }
                    ],
                    "displayVendorId": 452,
                    "vendorId": 2,
                },
            }
        ],
        "associatedCompany": {
            "companyId": company_id,
            "companyName": company_name,
        },
        "region": {
            "regionName": region_name,
            "displayName": region_display_name,
            "regionId": region_id,
        },
    }
    try:
        response = commvault_api_client.post(
            "StoragePool",
            params={"Action": "create"},
            data=payload,
        )
        result = filter_create_storage_pool_response(response)
        if not result["success"]:
            logger.error(f"Storage pool creation returned errorCode {result['errorCode']}")
            return {
                "error": True,
                "message": (
                    f"Storage pool creation failed (error code {result['errorCode']}). "
                    "Check that the company, region, and permissions are correct."
                ),
            }
        return result
    except Exception as e:
        logger.error(f"Error creating storage pool for plan: {e}")
        return {"error": True, "message": f"Failed to create storage pool: {str(e)}"}


def get_plan_storage_pool_list() -> dict:
    """List storage pools that are eligible for backing a server plan.

    Call this as a **fallback** during the plan-creation subflow if the
    storage pool ID cannot be determined from ``create_storage_pool_for_plan``'s
    response.  The returned list can also be used when a user wants to attach
    an existing storage pool to a new plan rather than creating a new one.

    Returns an LLM-friendly list with storagePoolId, storagePoolName,
    storagePolicyId, storagePolicyName, region, status, and storageType.
    """
    try:
        response = commvault_api_client.get(
            "StoragePool",
            params={"storageSubType": 2, "operationType": 2},
        )
        return filter_plan_storage_pool_list(response)
    except Exception as e:
        logger.error(f"Error listing plan storage pools: {e}")
        return {"error": True, "message": f"Failed to list storage pools: {str(e)}"}


def create_server_plan(
    plan_name: Annotated[str, Field(description="Name for the new server plan, e.g. 'my-connection-plan'.")],
    storage_pool_id: Annotated[int, Field(description="Commvault storage policy ID returned by create_storage_pool_for_plan.")],
    storage_pool_name: Annotated[str, Field(description="Storage policy name returned by create_storage_pool_for_plan.")],
    retention_days: Annotated[int, Field(description="Retention period in days. Defaults to 30.")] = 30,
) -> dict:
    """Create a new Commvault server plan backed by an existing storage pool.

    This is **step 2** of the plan-creation subflow within AWS onboarding.

    Call this only after ``create_storage_pool_for_plan`` succeeds.  Pass the
    ``storagePolicyId`` as ``storage_pool_id`` and ``storagePolicyName`` as
    ``storage_pool_name``.

    The plan is configured with a standard daily incremental schedule,
    90-day synthetic full, and automatic transaction-log backup for databases.
    Customisation is not required for the onboarding flow.

    Returns a compact summary with planId, planName, and guid.  Carry
    ``planId`` forward into ``create_aws_protection_group``.
    """
    payload = {
        "planName": plan_name,
        "backupDestinations": [
            {
                "backupDestinationName": "Primary",
                "retentionPeriodDays": retention_days,
                "useExtendedRetentionRules": False,
                "overrideRetentionSettings": True,
                "backupStartTime": -1,
                "storagePool": {
                    "id": storage_pool_id,
                    "name": storage_pool_name,
                },
                "storageType": "CLOUD",
            }
        ],
        "rpo": {
            "backupFrequency": {
                "schedules": [
                    {
                        "backupType": "INCREMENTAL",
                        "scheduleOperation": "ADD",
                        "schedulePattern": {
                            "scheduleFrequencyType": "DAILY",
                            "startTime": 75600,
                            "frequency": 1,
                        },
                        "forDatabasesOnly": False,
                    },
                    {
                        "backupType": "TRANSACTIONLOG",
                        "scheduleOperation": "ADD",
                        "schedulePattern": {
                            "scheduleFrequencyType": "AUTOMATIC",
                            "maxBackupIntervalInMins": 240,
                        },
                        "forDatabasesOnly": True,
                        "subType": "automatic",
                        "scheduleOption": {
                            "useDiskCacheForLogBackups": True,
                            "commitFrequencyInHours": 8,
                            "logsDiskUtilizationPercent": 80,
                            "logFilesThreshold": 50,
                            "deleteLogsByAppLvlConfig": False,
                        },
                    },
                    {
                        "backupType": "SYNTHETICFULL",
                        "scheduleOperation": "ADD",
                        "schedulePattern": {
                            "scheduleFrequencyType": "AUTOMATIC",
                            "startTime": 75600,
                            "frequency": 90,
                            "daysBetweenSyntheticFulls": 90,
                        },
                        "forDatabasesOnly": False,
                        "scheduleName": "Synthetic Fulls",
                    },
                ]
            },
            "backupWindow": [],
            "fullBackupWindow": [],
        },
    }
    try:
        response = commvault_api_client.post("V4/ServerPlan", data=payload)
        result = filter_create_server_plan_response(response)
        if not result["success"]:
            logger.error(f"Server plan creation returned no plan ID: {response}")
            return {
                "error": True,
                "message": (
                    "Plan creation did not return a plan ID. "
                    "Check that the storage pool is valid and permissions are sufficient."
                ),
            }
        return result
    except Exception as e:
        logger.error(f"Error creating server plan: {e}")
        return {"error": True, "message": f"Failed to create server plan: {str(e)}"}


PLAN_MANAGEMENT_TOOLS = [
    get_plan_list,
    get_plan_properties,
    create_storage_pool_for_plan,
    get_plan_storage_pool_list,
    create_server_plan,
]