---
name: aws-onboarding
description: 'Guide the user through onboarding an AWS Organization to Commvault, and optionally setting up a protection group. Use when the user says: onboard AWS, set up AWS cloud connection, connect AWS, add AWS account, AWS onboarding, set up protection group, protect AWS workloads.'
argument-hint: 'delegated admin AWS account ID (optional)'
---

# AWS Cloud Onboarding and Protection Group Setup

## When to Use
- User wants to onboard AWS or add an AWS cloud connection
- User mentions delegated admin account, AWS Organization, or AWS CFT permissions
- User wants to set up a protection group after onboarding

## Tools Available (via MCP)
- `get_aws_permissions_cft` — fetches CFT template URL and IAM role/user ARN details
- `deploy_commvault_access_cft` — deploys the Commvault cross-account IAM role CFT stack via boto3
- `get_commvault_access_cft_status` — polls the status of the access role CFT stack
- `validate_aws_cloud_credentials` — validates Commvault can assume the IAM role
- `browse_aws_cloud_accounts` — confirms account discovery through the IAM role
- `create_aws_cloud_connection` — creates the final cloud connection
- `list_aws_cloud_connections` — lists existing AWS cloud connections to select from
- `list_aws_workloads` — lists available AWS workload types grouped by category
- `list_eligible_plans` — lists backup plans eligible for an AWS protection group
- `create_storage_pool_for_plan` — creates a storage pool required for a new plan
- `create_server_plan` — creates a new server plan backed by the storage pool
- `create_aws_protection_group` — creates the protection group
- `start_aws_protection_group_backup` — starts an initial backup for the created protection group
- `list_org_units` — lists AWS Organizational Units under a given parent (pass `"root"` for top-level OUs)
- `check_member_stackset_status` — checks whether `CommvaultMemberAccountDiscovery` StackSet is deployed to an OU
- `deploy_member_account_stackset` — creates/updates and deploys the StackSet to a target OU
- `get_stackset_deployment_status` — polls the progress of an active StackSet deployment operation

---

## User Communication Style

**Follow these rules for every message to the user throughout this flow:**

1. **Product-first language.** Use milestone names: `Connect AWS`, `Validate Access`, `Discover Accounts`, `Create Connection`, `Choose Workloads`, `Choose Plan`, `Create Protection Group`, `Start Backup`. Never say "call the API" or use tool names in user-facing messages.

2. **Never expose raw data.** Do not paste JSON, API response objects, internal IDs, ARNs, or payload fields into user messages. Retain them internally for subsequent tool calls.

3. **One question at a time.** After each action, present one clear ask or confirmation. Do not bundle multiple questions.

4. **One status format.** After each action use exactly `**{Stage} — {State}**` on its own line, optionally followed by one short sentence. Example:
   > **Connect AWS — Done**
   > The Commvault access role has been deployed.

   Stages: `Connect AWS`, `Validate Access`, `Discover Accounts`, `Create Connection`, `Choose Workloads`, `Choose Plan`, `Create Protection Group`, `Start Backup`. States: `Working…`, `Done`, `Skipped`, `Needs attention`. Never use the older `Done:` / `Next:` prefix style.

5. **Show IDs only when needed.** Show a connection or plan ID only if two items share the same display name. Otherwise use names only.

6. **Plain-language errors.** On failure, say what the user should check in plain terms. No stack traces or raw error strings.

---

## Procedure

### Kickoff

When the user triggers this flow, send this single opening message before asking for anything:

> **Connect AWS — Working…**
> I'll set up the access role, validate connectivity, deploy member-account discovery, create the connection, and start the first backup. Takes ~5 minutes.

Then proceed immediately to Step 1.

---

### Step 1 — Get the account ID

If the user has not provided it, ask:

> To get started, I need your 12-digit AWS delegated admin account ID. What is it?

---

### Step 2 — Deploy the Commvault access role (CFT)

Call `get_aws_permissions_cft` with the account ID.

Retain internally (never show to user):
- `connectionTypes.organization.cftQuickCreateUrl` → the CFT S3 template URL
- `connectionTypes.organization.iamRoleArn` → the hosted-infra IAM role ARN
- `connectionTypes.organization.externalId` → the external ID
- `memberAccountSetup.templateUrl` → the StackSet template URL (used later in Step 4)

#### Step 2a — Check and deploy

Call `deploy_commvault_access_cft` with:
- `template_url` = `connectionTypes.organization.cftQuickCreateUrl`
- `infra_role_arn` = `connectionTypes.organization.iamRoleArn`
- `external_id` = `connectionTypes.organization.externalId`

**If `alreadyDeployed: true`:**

> **Connect AWS — Already set up**
> The Commvault access role is already deployed. Let's validate connectivity.

Skip to Step 3.

**If `inProgress: true`:**

> **Connect AWS — In progress**
> A stack deployment is already running. I'll monitor it for you.

Proceed to Step 2b.

**If `success: true`:**

> **Connect AWS — Deploying**
> Setting up the Commvault access role in your AWS account. I'll check back in a moment.

Proceed to Step 2b.

**If `error` is present:** Say what went wrong in plain language and ask the user to verify their AWS CLI credentials are configured for the delegated admin account, then retry.

#### Step 2b — Poll until the stack is ready

Repeat until terminal:

1. Wait ~15 seconds.
2. Call `get_commvault_access_cft_status`.
3. Show a brief status line: `⏳ Setting up access role… ({status})`

On `done: true` (CREATE_COMPLETE):

> **Connect AWS — Done**
> The Commvault access role has been deployed. Let's validate connectivity.

Proceed to Step 3.

On `failed: true`:

> **Connect AWS — Failed**
> The access role deployment failed. Please check that your AWS CLI credentials have permission to create IAM roles and CloudFormation stacks, then let me know to retry.

Stop and wait for user to resolve before retrying from Step 2a.

---

### Step 3 — Validate AWS access

Call `validate_aws_cloud_credentials` with the account ID.

On success:

> **Validate Access — Done**
> Commvault can now access your AWS account. Let's set up member account discovery.

On failure:

> **Validate Access — Needs attention**
> Commvault could not assume the IAM role yet. Please verify the access role stack status is **CREATE_COMPLETE** in CloudFormation, then let me know and I'll retry.

---

### Step 4 — Set up member account discovery (StackSet)

The goal is to deploy `CommvaultMemberAccountDiscovery` to the accounts inside the default Organizational Unit `ou-anxa-qikxlrp2`. This step is **idempotent**: if the StackSet is already healthy for that OU it is skipped entirely.

Use values retained from Step 2's `memberAccountSetup`:
- `templateUrl` → `template_url`
- `hostedInfraRoleArn` → `infra_role_arn`
- `hostedInfraUserArn` → `infra_user_arn`

#### Step 4a — Use the default target OU

Use `ou-anxa-qikxlrp2` as the target OU unless the user explicitly provides a different OU ID.

---

#### Step 4b — Check whether the StackSet is already deployed

Call `check_member_stackset_status` with `target_ou_id = "ou-anxa-qikxlrp2"` unless the user provided a different OU.

**Case 1 — `alreadyDeployed: true`**

> **Discover Accounts — Already set up**
> The Commvault member-account StackSet is already deployed across **{succeeded}** account(s) in that OU. No action needed.

Skip to Step 5.

**Case 2 — `notDeployed: true`**

Proceed to Step 4c.

**Case 3 — `status: "RUNNING"`**

> **Discover Accounts — In progress**
> A StackSet deployment is already running ({succeeded} done, {running} in progress, {failed} failed). I'll monitor it for you.

Jump to the polling loop in Step 4d.

**Case 4 — `status: "FAILED"`**

> **Discover Accounts — Previous deployment had errors**
> The last deployment left **{failed}** failed instance(s) ({failedAccounts}).
>
> I'll retry the deployment now.

Proceed directly to Step 4c.

---

#### Step 4c — Deploy the StackSet

Call `deploy_member_account_stackset` with:
- `target_ou_id` = `"ou-anxa-qikxlrp2"` unless the user provided a different OU
- `template_url` = `memberAccountSetup.templateUrl` from Step 2
- `infra_role_arn` = `memberAccountSetup.hostedInfraRoleArn` from Step 2
- `infra_user_arn` = `memberAccountSetup.hostedInfraUserArn` from Step 2
- `deployment_region` = `"us-east-1"` (default)

On success:
> **Discover Accounts — Deploying**
> StackSet deployment started. I'll monitor progress every 30 seconds.

Proceed to Step 4d.

On error: Say what went wrong in plain language, verify AWS CLI credentials have `cloudformation:*` permissions, and wait for the user to confirm before retrying from Step 4c.

---

#### Step 4d — Monitor deployment progress (polling loop)

Repeat until `status` is `SUCCEEDED`, `FAILED`, or `STOPPED`:

1. Wait ~30 seconds.
2. Call `get_stackset_deployment_status` with the `operationId` from Step 4c.
3. Display a compact progress update:
   > ⏳ Deploying… {succeeded} succeeded · {running} running · {failed} failed · {pending} pending

On `status: "SUCCEEDED"`:
> **Discover Accounts — Done**
> StackSet deployed successfully to all **{succeeded}** account(s) in the OU.

On `status: "FAILED"`:
> **Discover Accounts — Completed with errors**
> Deployment finished: **{succeeded}** succeeded, **{failed}** failed.
> Failed accounts: {failedAccounts}
>
> You can retry or continue — Commvault will still discover accounts where the stack succeeded.

On `status: "STOPPED"`:
> **Discover Accounts — Stopped**
> The deployment was stopped before completion. Would you like me to retry?

After any terminal state, continue to Step 5.

---

### Step 5 — Verify account discovery

Call `browse_aws_cloud_accounts` with the account ID.

On success (accounts returned), summarize concisely — show the count and a short sample, not the full list:

> **Discover Accounts — Done**
> Commvault discovered {N} AWS accounts. Ready to create the cloud connection.

If zero accounts are returned:

> **Discover Accounts — Needs attention**
> No accounts were found yet. Please verify the StackSet deployed successfully in the target OU, then let me know and I'll retry.

---

### Step 6 — Name and create the cloud connection

Recommend the name `aws-org-{accountId}` and ask for a simple confirmation:

> I'll name this cloud connection **aws-org-{accountId}**. Proceed?

- **Yes** → use the suggested name.
- **No** → ask "What name would you like?" and use the answer.

Call `create_aws_cloud_connection` with the confirmed name and account ID.

On success, use the `summary` from the response to compose the message. Show the job link only if `summary.discoveryJobUrl` is present.

> **Create Connection — Done**
> The cloud connection **{summary.connectionName}** has been created and account discovery has started. Commvault will begin discovering resources across your member accounts.
>
> You can track the discovery job here: [{summary.discoveryJobUrl}]({summary.discoveryJobUrl})

If `summary.discoveryJobUrl` is absent, omit the tracking link:

> **Create Connection — Done**
> The cloud connection **{summary.connectionName}** has been created and account discovery has started.

Immediately continue to the protection group steps — do not ask the user if they want one.

---

## Protection Group Setup (Steps 7–10)

Continue automatically from Step 6. The connection is already available — do not call `list_aws_cloud_connections` or ask the user to pick a connection again.

### Step 7 — Choose workloads

Call `list_aws_workloads`. Group the results by category internally. Count the total number of workloads (N). Do not show IDs.

Present the full list **inline per category** (comma-separated workload names on one line per category), then ask one question:

> **Choose Workloads**
> Commvault can protect all {N} AWS workloads across these categories:
>
> **File Servers:** Amazon Elastic File System
> **Virtualization:** Amazon EC2
> **Databases:** Amazon Aurora and RDS Snapshot, Amazon DynamoDB, Amazon Redshift
> _(list every category and its workloads inline — one line per category)_
>
> Should we protect all of them? Say **yes** to include everything.

- **User says yes / all / everything** → use all N workloads and confirm:
  > **Choose Workloads — Done**
  > Selected: all {N} AWS workloads.

- **User names workloads to exclude or a specific subset** → build the subset from their answer and confirm:
  > **Choose Workloads — Done**
  > Selected: {count} workloads — {comma-separated names}.

Build the workloads JSON array internally from the confirmed selection. Do not show the JSON to the user.

---

### Step 8 — Choose or create a backup plan

Call `list_eligible_plans`.

#### 8a — Plans exist (`totalPlans > 0`)

Internally select exactly one plan as the recommendation using this priority order (never narrate the selection logic):
1. Prefer the plan whose `planSummary` or `planName` contains "cloud" or "aws" (case-insensitive).
2. Otherwise, prefer the plan with the lowest non-zero `rpoInMinutes` (most frequent backup cadence).
3. On a tie, pick the first in the list.
4. If only one plan exists, it is always the recommendation.

Retain the recommended plan's `planId` and `planName` internally for use in Steps 9 and 10.

Format RPO from `rpoInMinutes`:
- 0 → `No scheduled RPO`
- < 60 → `Every {N} minutes`
- < 1440 → `Every {H}h {M}m`
- 1440 → `Daily`
- 10080 → `Weekly`
- 43800 → `Monthly`
- ≥ 525600 → `Yearly`

**Best-fit evaluation** (evaluate internally — never expose the criteria or label to the user):
A plan is a strong fit if **all three** of the following are true:
- `rpoInMinutes` ≤ 1440 (daily or more frequent)
- `numCopies` ≥ 2
- The `planSummary` text, read holistically, indicates adequate protection (e.g., mentions retention, tiering, multi-copy, or cloud-grade backup)

**Show only the recommended plan.** Do not list other plans.

**If the recommended plan is a strong fit:**

> **Choose Plan**
> Our recommended plan for your AWS workloads is **{planName}** — {rpoLabel}, {numCopies} copies.
> {planSummary}
>
> This looks like the best match for your use case. Would you like to use it, or create a new tailored plan instead?

**If the recommended plan is not a strong fit** (derive the gap in plain terms from `planSummary` and the criteria — e.g., infrequent cadence, single copy, missing retention info):

> **Choose Plan**
> **{planName}** is the closest available plan — {rpoLabel}, {numCopies} copies.
> {planSummary}
>
> It may not be the ideal fit: {plain-language reason}. Would you like to use it anyway, or let me create a new plan optimised for daily backups, 2 copies, and 30-day retention?

- **User chooses the recommended plan** → retain its `planId` and `planName`. Skip to Step 9.
- **User wants a new plan** → proceed to Plan Creation (Step 8b).

#### 8b — No plans (`totalPlans == 0`) or user wants a new plan

Proceed directly into plan creation — do not ask for confirmation first.

If `totalPlans == 0`:
> **Choose Plan**
> No backup plans are available yet. I'll create a tailored plan — daily backups, 2 copies, 30-day retention.

If the user chose to create a new plan:
> **Choose Plan — Working…**
> Creating a new plan optimised for daily backups, 2 copies, and 30-day retention.

Then continue immediately to the Plan Creation Subflow without waiting for a reply.

---

#### Plan Creation Subflow (fully automatic — no user prompts)

**Name-generation rule:** sanitize the cloud connection name — lowercase it, replace spaces and special characters with hyphens, strip leading/trailing hyphens. Use this base for all names silently; do not ask the user for any input during creation.

Derived names (all internal, never shown until creation is complete):
- Storage pool name: `{sanitizedConnectionName}-storage`
- Plan name: `{sanitizedConnectionName}-plan`
- Region: `us-east-1` / `US East (N. Virginia)` / id `50` (fixed default, never ask)
- Company: use `companyId` and `companyName` from the retained cloud connection

**Step 8c — Create storage pool silently**

Call `create_storage_pool_for_plan` immediately with:
- `storage_name` = `{sanitizedConnectionName}-storage`
- `company_id` and `company_name` from the retained cloud connection
- `region_name` = `us-east-1`, `region_display_name` = `US East (N. Virginia)`, `region_id` = `50`

Do not say anything to the user while this call is in progress.

On failure:
> **Create Plan — Needs attention**
> I wasn't able to create the storage pool. Please check that your Commvault account has permission to create storage, then let me know and I'll retry.

Do not proceed to plan creation if storage creation fails.

**Step 8d — Create server plan silently**

Call `create_server_plan` immediately with:
- `plan_name` = `{sanitizedConnectionName}-plan`
- `storage_pool_id` = `storagePolicyId` from Step 8c response
- `storage_pool_name` = `storagePolicyName` from Step 8c response
- `retention_days` = `30`

Do not say anything to the user while this call is in progress.

On success, show a single status line:
> **Create Plan — Done**
> Plan **{planName}** is ready. Let's set up the protection group.

Retain the `planId` internally. Continue to Step 9.

On failure:
> **Create Plan — Needs attention**
> The plan could not be created. Note: the storage pool **{storageName}** was created and will need to be deleted in the Commvault console if you do not retry.

---

### Step 9 — Name and create the protection group

Recommend the name `{sanitizedConnectionName}-protection-group` and ask for a simple confirmation:

> I'll name this protection group **{sanitizedConnectionName}-protection-group**. Proceed?

- **Yes** → use the suggested name.
- **No** → ask "What name would you like?" and use the answer.

Call `create_aws_protection_group` with the confirmed name.

On success, say:

> **Create Protection Group — Done**
> The protection group **{name}** is now active and protecting your selected workloads.

Retain the protection group ID from the response internally. Continue immediately to Step 10 — do not ask the user if they want to start a backup.

---

### Step 10 — Start the initial backup

Call `start_aws_protection_group_backup` immediately (no additional prompt) with:
- `protection_group_id` — the created protection group's ID (from Step 9 response)
- `backup_level` — `FULL`
- `notify_user_on_job_completion` — `true`
- `job_description` — `Initial full after user onboarding`

On success, use `summary.jobUrl` from the response. Use `{planName}` retained from Step 8 and `{rpoLabel}` derived from the chosen plan's `rpoInMinutes` using the RPO format rules above. Show the link if present.

> **Start Backup — Done**
> The initial full backup job has been submitted.
>
> Track the job here: [{summary.jobUrl}]({summary.jobUrl})
>
> Based on your **{planName}** plan, your next scheduled backup will run **{rpoLabel}**. If any backup encounters errors, you'll be notified by email.
>
> Your AWS environment is now fully onboarded and protected by Commvault.

If `summary.jobUrl` is absent, omit the tracking link:

> **Start Backup — Done**
> The initial full backup job has been submitted. Based on your **{planName}** plan, your next scheduled backup will run **{rpoLabel}**. If any backup encounters errors, you'll be notified by email.
>
> Your AWS environment is now fully onboarded and protected by Commvault.
