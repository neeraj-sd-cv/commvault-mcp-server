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

from urllib.parse import urlparse, parse_qs, unquote

from src.logger import logger


def get_basic_job_details(api_response):
    """
    Extracts minimal, LLM-friendly job details from the API response.
    """
    jobs = api_response.get("jobs", [])
    basic_details = []
    for job in jobs:
        summary = job.get("jobSummary", {})
        basic_details.append({
            "jobId": summary.get("jobId"),
            "status": summary.get("status"),
            "jobType": summary.get("jobType"),
            "backupLevelName": summary.get("backupLevelName"),
            "jobStartTime": summary.get("jobStartTime"),
            "jobEndTime": summary.get("jobEndTime"),
            "clientName": summary.get("destinationClient", {}).get("clientName"),
            "storagePolicyName": summary.get("storagePolicy", {}).get("storagePolicyName"),
        })
    return {
        "totalJobsAvailable": api_response.get("totalRecordsWithoutPaging", 0),
        "jobsInThisResponse": basic_details
    }

def get_basic_client_group_details(api_response):
    """
    Extracts minimal, LLM-friendly client group details from the API response.
    """
    groups = api_response.get("groups", [])
    filtered = []
    for g in groups:
        cg = g.get("clientGroup", {})
        entity = cg.get("entityInfo", {}) if cg else {}
        filtered.append({
            "clientGroupId": cg.get("clientGroupId", g.get("Id")),
            "clientGroupName": g.get("name"),
            "clientCount": g.get("clientCount"),
            "companyName": entity.get("companyName")
        })
    return {"totalClientGroups": len(filtered), "clientGroups": filtered}

def filter_subclient_response(api_response):
    """
    Filters the subclient response to only include relevant fields for each subclient.
    """
    relevant_keys = [
        "clientName", "instanceName", "displayName", "backupsetId",
        "instanceId", "subclientId", "appName", "backupsetName",
        "subclientName"
    ]
    filtered = []
    for subclient in api_response.get("subClientProperties", []):
        entity = subclient.get("subClientEntity", {})
        filtered_entity = {k: entity.get(k) for k in relevant_keys if k in entity}
        filtered.append(filtered_entity)
    return {
        "subClientCount": api_response.get("filterQueryCount", 0),
        "subClients": filtered
    }

def filter_storage_pool_response(api_response):
    """
    Extracts minimal, LLM-friendly storage pool details from the API response.
    """
    pools = api_response.get("storagePoolList", [])
    filtered = []
    for pool in pools:
        pool_entity = pool.get("storagePoolEntity", {})
        region = pool.get("region", {})
        policy = pool.get("storagePolicyEntity", {})
        filtered.append({
            "storagePoolName": pool_entity.get("storagePoolName"),
            "storagePoolId": pool_entity.get("storagePoolId"),
            "totalFreeSpace": pool.get("totalFreeSpace"),
            "sizeOnDisk": pool.get("sizeOnDisk"),
            "status": pool.get("status"),
            "regionDisplayName": region.get("displayName"),
            "regionName": region.get("regionName"),
            "storagePolicyName": policy.get("storagePolicyName"),
            "storagePolicyId": policy.get("storagePolicyId"),
        })
    return {"storagePoolCount": len(filtered), "storagePools": filtered}

def format_report_dataset_response(api_response):
    """
    Formats the report dataset response for LLM consumption.
    Returns a list of dicts, each dict representing a record with column names as keys.
    """
    columns = api_response.get("columns", [])
    records = api_response.get("records", [])
    column_names = [col.get("name") for col in columns]
    formatted_records = []
    for record in records:
        formatted_records.append({column_names[i]: record[i] for i in range(len(column_names))})
    return {
        "totalRecordCount": api_response.get("totalRecordCount", 0),
        "records": formatted_records
    }

def transform_sla_data(api_response):
    """
    Transform SLA Data API response to a simplified format with SLA percentage.
    
    Args:
        api_response (dict): The original API response
        
    Returns:
        dict: Simplified data with SLA percentage
    """
    try:
        result = {}
        records = api_response.get('records', [])
        
        for record in records:
            # Each record is a list where index 2 is the SLA Status and index 3 is the CurrentCount
            sla_status = record[2]
            current_count = record[3]
            
            # Only consider Met SLA and Missed SLA, ignoring the other values for now
            if sla_status in ["Met SLA", "Missed SLA"]:
                result[sla_status] = current_count
        
        met_sla_count = result.get("Met SLA", 0)
        missed_sla_count = result.get("Missed SLA", 0)
        total_count = met_sla_count + missed_sla_count
        
        if total_count > 0:
            sla_percentage = (met_sla_count / total_count) * 100
        else:
            sla_percentage = 0
        
        result["SLA Percentage"] = round(sla_percentage, 2)
        return result
    except Exception as e:
        logger.error(f"Error transforming SLA data: {e}")
        raise Exception("Error transforming SLA data")

def compute_security_score(api_response):
    params = [
        p
        for cat in api_response.get("securityCategories", [])
        for p in cat.get("parameter", [])
    ]
    total = len(params)
    if total == 0:
        raise Exception("Some error occurred. Please try again later.")
    failures = sum(1 for p in params if p.get("status") == 2)
    return round((total - failures) / total * 100)

def filter_client_list_response(response):
    """
    Filters the client list response to return only clientName, clientId, displayName, hostName, clientGUID, and companyId.
    """
    filtered_clients = []
    for item in response.get("clientProperties", []):
        client_entity = item.get("client", {}).get("clientEntity", {})
        entity_info = client_entity.get("entityInfo", {})
        filtered_clients.append({
            "clientName": client_entity.get("clientName"),
            "clientId": client_entity.get("clientId"),
            "hostName": client_entity.get("hostName")
        })
    return {"clients": filtered_clients}

def filter_schedules_response(response):
    """
    Filters the schedules API response to return only relevant information with LLM-friendly key names.
    """
    schedules = []
    for item in response.get("taskDetail", []):
        task = item.get("task", {})
        sub_tasks = item.get("subTasks", [])
        filtered_subtasks = []
        for sub in sub_tasks:
            sub_task = sub.get("subTask", {})
            filtered_subtasks.append({
                "scheduleName": sub_task.get("subTaskName"),
                "scheduleId": sub_task.get("subTaskId"),
                "operationType": sub_task.get("operationType"),
                "nextRunTime": sub.get("nextScheduleTime"),
            })

        # Only add if "policyName" does not contain "System Created"
        policy_name = task.get("taskName", "")
        description = task.get("description", "")
        if "system created" not in policy_name.lower() and "system created" not in description.lower():
            schedules.append({
            "policyName": policy_name,
            "policyId": task.get("taskId"),
            "description": description,
            "schedules": filtered_subtasks,
            })
    return {"totalPolicies": len(schedules), "policies": schedules}

def filter_users_response(response):
    """
    Filters the users API response to return only relevant, LLM-friendly information.
    """
    users = []
    for user in response.get("users", []):
        users.append({
            "userId": user.get("id"),
            "userName": user.get("name"),
            "email": user.get("email"),
            "fullName": user.get("fullName"),
            "lastLoginTime": user.get("lastLoggedIn"),
            "companyId": user.get("company", {}).get("id")
        })
    return {"totalUsers": response.get("numberOfUsers", len(users)), "users": users}

def filter_user_groups_response(response):
    """
    Filters the user groups API response to return only relevant, LLM-friendly information.
    """
    user_groups = []
    for group in response.get("userGroups", []):
        user_groups.append({
            "userGroupId": group.get("id"),
            "userGroupName": group.get("name"),
            "companyId": group.get("company", {}).get("id")
        })
    return {"totalUserGroups": response.get("numberOfUserGroups", len(user_groups)), "userGroups": user_groups}

def filter_security_associations_response(api_response):
    """
    Filters the security associations response to return only relevant, LLM-friendly information.
    """
    filtered = []
    for assoc in api_response.get("associations", []):
        entity_list = assoc.get("entities", {}).get("entity", [])
        entities = []
        for ent in entity_list:
            # Only keep keys that are not internal metadata (no _type_, no flags unless needed)
            entity = {k: v for k, v in ent.items() if not k.startswith("_") and k != "flags"}
            # If 'flags' contains 'includeAll', keep it
            if "flags" in ent and ent["flags"].get("includeAll"):
                entity["includeAll"] = True
            entities.append(entity)
        properties = assoc.get("properties", {})
        filtered_assoc = {"entities": entities}
        # Only keep relevant properties
        if "isCreatorAssociation" in properties:
            filtered_assoc["isCreatorAssociation"] = properties["isCreatorAssociation"]
        if "role" in properties:
            role = properties["role"]
            filtered_assoc["role"] = {k: v for k, v in role.items() if k in ["roleId", "roleName", "disabled"] or (k == "flags" and role.get("flags", {}).get("disabled")) }
        if "categoryPermission" in properties:
            filtered_assoc["categoryPermission"] = [
                {k: v for k, v in perm.items() if k in ["permissionName", "permissionId"]}
                for perm in properties["categoryPermission"].get("categoriesPermissionList", [])
            ]
        filtered.append(filtered_assoc)
    return {"associations": filtered}


def _connection_type_label(raw: str) -> str:
    """Convert an API connectionType value to a human-readable label."""
    mapping = {
        "OrganizationLevel": "Organization-level",
        "AccountLevel": "Account-level",
    }
    return mapping.get(raw, raw)


def _rpo_label(rpo_minutes: int) -> str:
    """Convert rpoInMinutes to a short, human-readable string."""
    if rpo_minutes == 0:
        return "No scheduled RPO"
    if rpo_minutes < 60:
        return f"Every {rpo_minutes} minute{'s' if rpo_minutes != 1 else ''}"
    if rpo_minutes < 1440:
        hours, mins = divmod(rpo_minutes, 60)
        label = f"Every {hours}h"
        if mins:
            label += f" {mins}m"
        return label
    if rpo_minutes < 10080:
        days = rpo_minutes // 1440
        return f"Every {days} day{'s' if days != 1 else ''}"
    if rpo_minutes < 43800:
        weeks = rpo_minutes // 10080
        return f"Every {weeks} week{'s' if weeks != 1 else ''}"
    if rpo_minutes < 525600:
        months = rpo_minutes // 43800
        return f"Every {months} month{'s' if months != 1 else ''}"
    return "Yearly"


def filter_aws_cloud_connections_response(api_response):
    """
    Extracts minimal, LLM-friendly AWS cloud connection details from the API response.
    Includes a displayLabel suitable for presenting to users without exposing IDs.
    """
    connections = api_response.get("cloudConnections", [])
    filtered = []
    for conn in connections:
        company = conn.get("company", {})
        conn_type_raw = conn.get("connectionType", "")
        conn_type_label = _connection_type_label(conn_type_raw)
        company_name = company.get("name", "")
        display_name = conn.get("displayName") or conn.get("name", "")
        filtered.append({
            "id": conn.get("id"),
            "name": conn.get("name"),
            "displayName": display_name,
            "cloudType": conn.get("cloudType"),
            "connectionType": conn_type_raw,
            "companyName": company_name,
            "companyId": company.get("id"),
            "displayLabel": f"{display_name} — {conn_type_label}, {company_name}",
        })
    return {"totalConnections": len(filtered), "cloudConnections": filtered}


def filter_aws_solutions_response(api_response):
    """
    Restructures the AWS solutions/workloads response into a grouped,
    LLM-friendly format keyed by category. Includes workloadCount per category.
    """
    categories = []
    for category in api_response.get("id", []):
        workloads = [
            {"id": w.get("id"), "name": w.get("name")}
            for w in category.get("associatedWorkloads", [])
        ]
        categories.append({
            "categoryId": category.get("id"),
            "categoryName": category.get("name"),
            "workloadCount": len(workloads),
            "workloads": workloads,
        })
    return {"categories": categories}


def filter_plan_storage_pool_list(api_response):
    """
    Extracts minimal, LLM-friendly storage pool details for plan creation.
    Handles both bare-list and storagePoolList-wrapped response shapes.
    """
    pools = api_response if isinstance(api_response, list) else api_response.get("storagePoolList", [])
    filtered = []
    for pool in pools:
        pool_entity = pool.get("storagePoolEntity", {})
        policy_entity = pool.get("storagePolicyEntity", {})
        region = pool.get("region", {})
        filtered.append({
            "storagePoolId": pool_entity.get("storagePoolId"),
            "storagePoolName": pool_entity.get("storagePoolName"),
            "storagePolicyId": policy_entity.get("storagePolicyId"),
            "storagePolicyName": policy_entity.get("storagePolicyName"),
            "regionName": region.get("regionName"),
            "regionDisplayName": region.get("displayName"),
            "regionId": region.get("regionId"),
            "status": pool.get("status"),
            "storageType": pool.get("storageType"),
        })
    return {"totalPools": len(filtered), "storagePools": filtered}


def filter_create_storage_pool_response(api_response):
    """
    Extracts compact summary from a storage pool creation response.
    Returns storagePolicyName, storagePolicyId, copyId, copyName and
    a success flag based on errorCode == 0.
    """
    error = api_response.get("error", {})
    error_code = error.get("errorCode", -1)
    copy = api_response.get("archiveGroupCopy", {})
    return {
        "success": error_code == 0,
        "errorCode": error_code,
        "storagePolicyName": copy.get("storagePolicyName"),
        "storagePolicyId": copy.get("storagePolicyId"),
        "copyId": copy.get("copyId"),
        "copyName": copy.get("copyName"),
    }


def filter_create_server_plan_response(api_response):
    """
    Extracts compact summary from a server plan creation response.
    Returns planId, planName and guid, plus a success flag.
    """
    plan = api_response.get("plan", {})
    plan_id = plan.get("id")
    return {
        "success": plan_id is not None,
        "planId": plan_id,
        "planName": plan.get("name"),
        "guid": plan.get("GUID"),
    }


def filter_org_units_response(api_response):
    """
    Normalises the Organizations ListOrganizationalUnitsForParent response into
    a minimal LLM-friendly list of {ouId, ouName} dicts.
    """
    ous = api_response.get("OrganizationalUnits", [])
    return {
        "totalOUs": len(ous),
        "ous": [{"ouId": ou.get("Id"), "ouName": ou.get("Name")} for ou in ous],
    }


def filter_stackset_status_response(operation_response, instances_response):
    """
    Combines describe_stack_set_operation and list_stack_instances responses into
    a compact progress summary keyed by status counts.
    """
    operation = operation_response.get("StackSetOperation", {})
    overall_status = operation.get("Status", "UNKNOWN")

    counts = {"SUCCEEDED": 0, "RUNNING": 0, "FAILED": 0, "PENDING": 0,
              "CANCELLED": 0, "STOPPED": 0}
    failed_accounts = []

    for summary in instances_response.get("Summaries", []):
        detail = summary.get("StackInstanceStatus", {}).get("DetailedStatus", "UNKNOWN")
        if detail in counts:
            counts[detail] += 1
        elif detail in ("INOPERABLE", "FAILED_IMPORT"):
            counts["FAILED"] += 1
        if detail in ("FAILED", "INOPERABLE", "FAILED_IMPORT"):
            account = summary.get("Account")
            if account:
                failed_accounts.append(account)

    total = sum(counts.values())
    result = {
        "status": overall_status,
        "totalInstances": total,
        "succeeded": counts["SUCCEEDED"],
        "running": counts["RUNNING"],
        "failed": counts["FAILED"],
        "pending": counts["PENDING"],
    }
    if failed_accounts:
        result["failedAccounts"] = failed_accounts
    return result


def filter_member_stackset_check(exists, instances_response, target_ou_id):
    """
    Interprets list_stack_instances output scoped to a specific OU and returns
    a plain state dict:
      alreadyDeployed / notDeployed / status (RUNNING|FAILED)
    """
    if not exists:
        return {"exists": False, "notDeployed": True}

    summaries = [
        s for s in instances_response.get("Summaries", [])
        if s.get("OrganizationalUnitId") == target_ou_id
    ]

    if not summaries:
        return {"exists": True, "notDeployed": True}

    counts = {"SUCCEEDED": 0, "RUNNING": 0, "FAILED": 0, "PENDING": 0}
    failed_accounts = []

    for s in summaries:
        detail = s.get("StackInstanceStatus", {}).get("DetailedStatus", "UNKNOWN")
        if detail == "SUCCEEDED":
            counts["SUCCEEDED"] += 1
        elif detail in ("RUNNING",):
            counts["RUNNING"] += 1
        elif detail in ("FAILED", "INOPERABLE", "FAILED_IMPORT", "CANCELLED"):
            counts["FAILED"] += 1
            acct = s.get("Account")
            if acct:
                failed_accounts.append(acct)
        else:
            counts["PENDING"] += 1

    total = len(summaries)

    if counts["RUNNING"] > 0:
        return {
            "status": "RUNNING",
            "totalInstances": total,
            "succeeded": counts["SUCCEEDED"],
            "running": counts["RUNNING"],
            "failed": counts["FAILED"],
            "pending": counts["PENDING"],
        }

    if counts["FAILED"] == 0 and counts["SUCCEEDED"] == total:
        return {"alreadyDeployed": True, "succeeded": total, "total": total}

    return {
        "status": "FAILED",
        "totalInstances": total,
        "succeeded": counts["SUCCEEDED"],
        "failed": counts["FAILED"],
        "pending": counts["PENDING"],
        "failedAccounts": failed_accounts,
    }


def filter_eligible_plans_response(api_response):
    """
    Extracts minimal, LLM-friendly plan details from the eligible plans API response.
    Includes a human-readable rpoLabel alongside the raw rpoInMinutes.
    """
    filtered = []
    for item in api_response.get("plans", []):
        plan = item.get("plan", {})
        rpo_minutes = item.get("rpoInMinutes", 0)
        filtered.append({
            "planId": plan.get("planId"),
            "planName": plan.get("planName"),
            "planSummary": plan.get("planSummary"),
            "numCopies": item.get("numCopies"),
            "rpoInMinutes": rpo_minutes,
            "rpoLabel": _rpo_label(rpo_minutes),
        })
    return {"totalPlans": len(filtered), "plans": filtered}


def filter_aws_permissions_cft_response(api_response):
    articles = api_response.get("cloudConnectionArticlesList", [])
    result = {}

    for article in articles:
        raw_type = article.get("cloudConnectionType", "")
        if raw_type == "ORGANIZATION":
            key = "organization"
        elif raw_type == "CLOUD_ACCOUNT":
            key = "account"
        else:
            key = raw_type

        entry = {
            "cftQuickCreateUrl": article.get("hostedInfrastructureCftUrl", ""),
            "iamRoleArn": article.get("iamRoleArn", ""),
            "externalId": article.get("externalId", ""),
            "userInstructions": (
                "Open the CloudFormation quick-create URL below in your AWS Console. "
                "Review the stack parameters and click 'Create stack'. "
                "Wait for the stack status to show CREATE_COMPLETE before confirming."
            ),
        }

        member = article.get("memberAccountArticles")
        if member:
            entry["memberAccountSetup"] = {
                "templateUrl": member.get("templateUrl", ""),
                "hostedInfraRoleArn": member.get("hostedInfraRoleArn", ""),
                "hostedInfraUserArn": member.get("hostedInfraUserArn", ""),
            }
            entry["memberAccountInstructions"] = (
                "Configure IAM role in Member AWS Accounts:\n\n"
                "1. Sign in to the delegated admin account and go to "
                "https://console.aws.amazon.com/cloudformation/home#/stacksets/create\n"
                "2. Select 'Specify template' and paste the Amazon S3 URL shown below.\n"
                "3. In the Parameters section, paste the Commvault Cloud IAM Role ARN "
                "and User ARN shown below.\n"
                "4. Proceed with default options and submit the StackSet.\n\n"
                "This creates IAM roles in member accounts for resource discovery, "
                "backup, and restore."
            )

        result[key] = entry

    return {"connectionTypes": result}


def parse_cft_quick_create_params(quick_create_url: str) -> dict:
    """Parse a CloudFormation quick-create URL into its component parts.

    Returns:
        {
          "templateUrl": str,
          "stackName": str,
          "params": [{"ParameterKey": str, "ParameterValue": str}, ...]
        }
    The URL fragment (after ``#``) contains the actual query string, e.g.:
    ``https://region.console.aws.amazon.com/cloudformation/home?region=us-east-1
      #/stacks/create/review?templateURL=...&stackName=...&param_Key=Value``
    """
    try:
        parsed = urlparse(quick_create_url)
        # The real query params live in the fragment after the ``?``
        fragment = parsed.fragment  # e.g. /stacks/create/review?templateURL=...
        if "?" in fragment:
            _, frag_query = fragment.split("?", 1)
        else:
            frag_query = ""

        qs = parse_qs(frag_query, keep_blank_values=True)

        template_url = unquote(qs.get("templateURL", [""])[0])
        stack_name = qs.get("stackName", ["CommvaultPermissionsStack"])[0]

        params = []
        for key, values in qs.items():
            if key.startswith("param_"):
                param_key = key[len("param_"):]
                params.append({"ParameterKey": param_key, "ParameterValue": values[0]})

        return {"templateUrl": template_url, "stackName": stack_name, "params": params}
    except Exception as exc:
        logger.warning(f"parse_cft_quick_create_params failed for URL: {exc}")
        return {"templateUrl": "", "stackName": "CommvaultPermissionsStack", "params": []}


def filter_virtual_machines_response(api_response):
    """Extract name and UUID for each virtual machine — the minimal set needed for
    the recovery flow. The agent uses the name list to ask the user which VM to
    restore, then matches the chosen name back to the UUID before calling the
    restore API.
    """
    vms = api_response.get("virtualMachines", [])
    filtered = [{"name": vm.get("name", ""), "UUID": vm.get("UUID", "")} for vm in vms]
    return {
        "virtualMachinesCount": api_response.get("virtualMachinesCount", len(filtered)),
        "virtualMachines": filtered,
    }
