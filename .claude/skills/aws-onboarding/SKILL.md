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
- `get_access_role_setup_steps` — returns browser quick-create URL and AWS CLI commands to deploy the Commvault access-role CFT (no AWS credentials required on the server)
- `get_member_discovery_setup_steps` — returns browser console steps and AWS CLI commands to deploy the CommvaultMemberAccountDiscovery StackSet (no AWS credentials required on the server)
- `validate_aws_cloud_credentials` — validates Commvault can assume the IAM role
- `browse_aws_cloud_accounts` — confirms account discovery through the IAM role
- `create_aws_cloud_connection` — creates the final cloud connection
- `list_aws_cloud_connections` — lists existing AWS cloud connections to select from
- `list_eligible_plans` — lists backup plans eligible for an AWS protection group
- `create_storage_pool_for_plan` — creates a storage pool required for a new plan
- `create_server_plan` — creates a new server plan backed by the storage pool
- `create_aws_protection_group` — creates the protection group
- `start_aws_protection_group_backup` — starts an initial backup for the created protection group
- `delete_aws_cloud_connection` — retires and permanently deletes a Commvault cloud connection (also removes its protection group)
- `cleanup_aws_demo_environment` — returns ordered AWS CLI commands to delete the CloudFormation stack and StackSet created during onboarding

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
- `connectionTypes.organization.cftQuickCreateUrl` → browser quick-create URL
- `connectionTypes.organization.iamRoleArn` → the hosted-infra IAM role ARN
- `connectionTypes.organization.externalId` → the external ID
- `connectionTypes.organization.memberAccountSetup.templateUrl` → StackSet template URL (used later in Step 4)
- `connectionTypes.organization.memberAccountSetup.hostedInfraRoleArn` → StackSet role ARN (Step 4)
- `connectionTypes.organization.memberAccountSetup.hostedInfraUserArn` → StackSet user ARN (Step 4)

#### Step 2a — Get setup steps

Call `get_access_role_setup_steps` with:
- `quick_create_url` = `connectionTypes.organization.cftQuickCreateUrl`
- `infra_role_arn` = `connectionTypes.organization.iamRoleArn`
- `external_id` = `connectionTypes.organization.externalId`

The tool returns a `browserUrl` and a `cliCommands` list.

#### Step 2b — Execute the setup

The agent always executes the CLI commands directly. Show the commands to the user before running them, then execute immediately — do not wait for user confirmation.

Inform the user of the optional manual alternative before executing:

> **Connect AWS — Working…**
> Running the following AWS CLI commands to deploy the access role. If you prefer to do this manually instead, you can use this link in your AWS Console (sign in as the delegated admin account) and click **Create stack**: [Open CloudFormation quick-create]({browserUrl})
>
> ```
> {cliCommands[0]}
> {cliCommands[1]}
> ```

Run `cliCommands[0]` (create-stack). Then poll using `cliCommands[1]` (describe-stacks) every 15 seconds until the output shows `CREATE_COMPLETE`.

Show progress while polling:
> ⏳ Setting up access role… ({current status})

On `CREATE_COMPLETE`:

> **Connect AWS — Done**
> The Commvault access role has been deployed. Let's validate connectivity.

Proceed to Step 3.

On `CREATE_FAILED` / `ROLLBACK_COMPLETE`:

> **Connect AWS — Needs attention**
> The access role stack failed to deploy. Please check that you are signed in to the AWS CLI as the delegated admin account (your session may have expired or the wrong profile may be active), then let me know to retry.

Stop and wait for user to resolve before retrying from Step 2a.

On any AWS CLI command error (e.g. authentication error, access denied, no credentials):

> **Connect AWS — Needs attention**
> The AWS CLI command failed. Please check that you are signed in to the AWS CLI as the delegated admin account (your session may have expired), then let me know to retry.

Stop and wait for user to resolve before retrying from Step 2a.

---

### Step 3 — Validate AWS access

Call `validate_aws_cloud_credentials` with the account ID.

On success:

> **Validate Access — Done**
> Commvault can now access your AWS account. Let's set up member account discovery.

On failure:

> **Validate Access — Needs attention**
> Commvault could not assume the IAM role yet. Please verify the access role stack status is **CREATE_COMPLETE** in CloudFormation (an expired or incorrect AWS CLI session during Step 2 could also cause this), then let me know and I'll retry.

---

### Step 4 — Set up member account discovery (StackSet)

The goal is to deploy `CommvaultMemberAccountDiscovery` to the accounts inside the default Organizational Unit `ou-anxa-qikxlrp2`. Use `ou-anxa-qikxlrp2` as the target OU unless the user explicitly provides a different OU ID.

#### Step 4a — Get setup steps

Call `get_member_discovery_setup_steps` with values retained from Step 2:
- `template_url` = `connectionTypes.organization.memberAccountSetup.templateUrl`
- `infra_role_arn` = `connectionTypes.organization.memberAccountSetup.hostedInfraRoleArn`
- `infra_user_arn` = `connectionTypes.organization.memberAccountSetup.hostedInfraUserArn`
- `target_ou_id` = `"ou-anxa-qikxlrp2"` (or the user-provided OU)
- `region` = `"us-east-1"` (default)

The tool returns `browserSteps` and a `cliCommands` list.

---

#### Step 4b — Execute the setup

The agent always executes the CLI commands directly. Show the commands to the user before running them, then execute immediately — do not wait for user confirmation.

Inform the user of the optional manual alternative before executing:

> **Discover Accounts — Working…**
> Running the following AWS CLI commands to deploy the member-account discovery StackSet. If you prefer to do this manually instead, here are the steps to follow in your AWS Console:
>
> {browserSteps}
>
> Otherwise, I'll run these commands now:
>
> ```
> {cliCommands[1]}
> {cliCommands[2]}
> {cliCommands[3]}
> ```

Run the commands from `cliCommands` in order:
1. Run `cliCommands[1]` (create-stack-set). If the error indicates the StackSet already exists, skip to step 2.
2. Run `cliCommands[2]` (create-stack-instances).
3. Poll using `cliCommands[3]` (list-stack-instances) every 30 seconds.

Show progress while polling:
> ⏳ Deploying… checking instance status across member accounts…

On all instances showing `SUCCEEDED`:

> **Discover Accounts — Done**
> StackSet deployed successfully across all member accounts in the OU.

On any instances showing `FAILED`:

> **Discover Accounts — Completed with errors**
> Some accounts failed deployment. You can retry or continue — Commvault will still discover accounts where the stack succeeded.

On any AWS CLI command error (e.g. authentication error, access denied, no credentials):

> **Discover Accounts — Needs attention**
> The AWS CLI command failed. Please check that you are signed in to the AWS CLI as the delegated admin account (your session may have expired), then let me know to retry.

Stop and wait for user to resolve before retrying from Step 4a.

If the user needs to find their OU ID, run the optional OU-discovery commands from `cliCommands[4]` and present the results as a plain list.

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

Derive a descriptive, human-readable name for the cloud connection from the conversation context — draw on whatever you know: the organisation or company name the user has mentioned, the purpose of the account, the AWS account ID, or any other relevant detail shared during this session. The goal is a name that is meaningful to this customer and unlikely to collide with existing connections (e.g. "acme-aws-org", "prod-aws-123456789012", "marketing-aws-org"). Do not use a rigid template.

Present the derived name for a simple confirmation:

> I'll name this cloud connection **{derivedName}**. Proceed?

- **Yes** → use the derived name.
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

On failure, silently call `list_aws_cloud_connections` and scan the results for a connection whose name matches the one just attempted.

If a name match is found:

> **Create Connection — Needs attention**
> The connection could not be created because a connection named **{name}** already exists. Would you like to use the existing connection, or choose a different name?

- User says use existing → retain that connection's `connectionId`, `companyId`, and `companyName` internally and continue to Step 7.
- User wants a different name → ask "What name would you like?" and retry `create_aws_cloud_connection` with the new name.

If no name match is found:

> **Create Connection — Needs attention**
> The connection could not be created. Please check that your Commvault account has permission to create cloud connections, then let me know and I'll retry.

---

## Protection Group Setup (Steps 7–10)

Continue automatically from Step 6. The connection is already available — do not call `list_aws_cloud_connections` or ask the user to pick a connection again.

When introducing this section to the user, briefly explain what a protection group is in plain, friendly language. Something like:

> Now we'll set up a **protection group** — think of it as a named list of cloud resources you want Commvault to watch over. It groups your AWS workloads together and ties them to a backup plan that controls how often they're backed up and how long those backups are kept. Once it's active, Commvault automatically runs backups on your schedule without you having to do anything further.

### Step 7 — Choose workloads

Ask a simple confirmation — do not enumerate workloads:

> **Choose Workloads**
> We'll protect all AWS workloads discovered in this connection. Can we proceed?

- **User confirms** → acknowledge and move on:
  > **Choose Workloads — Done**
  > All discovered workloads in this connection will be protected.

Pass `workloads_json = "[]"` and `all_cloud_accounts = true` to `create_aws_protection_group` in Step 9 — this tells Commvault to cover everything in the connection automatically.

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

When presenting the plan to the user, always include a plain-language explanation of what a backup plan is and what each term means. Weave these explanations naturally into the message — do not present them as a separate glossary block. Explain:
- **Backup plan**: the set of rules that governs how your cloud data is protected — how often backups run, how many independent copies are kept, and how long they are retained before being deleted.
- **RPO (Recovery Point Objective)**: how frequently a backup snapshot is taken. For example, "Daily" means a backup runs every day, so in the worst case you could lose at most one day's worth of changes if something goes wrong. A shorter RPO (e.g. every few hours) means less potential data loss.
- **Copies**: the number of independent backup copies Commvault keeps. Having 2 or more copies means if one storage location has a problem, your data is still safe in another.
- **Retention**: how long backups are kept before they are automatically removed. Longer retention lets you recover from mistakes or events discovered weeks or months later.

**If the recommended plan is a strong fit:**

> **Choose Plan**
> Before we continue, here's a quick primer on what a backup plan is and what you'll be agreeing to — this is all standard Commvault terminology, so don't worry if it's new to you.
>
> A **backup plan** is the rulebook for protecting your data: it defines how often backups run (the RPO), how many independent copies are kept for safety, and how long those copies are retained before being automatically deleted.
>
> Our recommended plan for your AWS workloads is **{planName}**:
> - **Backup frequency (RPO):** {rpoLabel} — {plain-language explanation of what this RPO means for their data risk, e.g. "a backup runs every day, so at most one day of changes could be at risk if something goes wrong"}
> - **Copies:** {numCopies} — {numCopies} independent copies of each backup are stored, so a single storage failure won't leave you without a recovery point
> - **Summary:** {planSummary}
>
> This looks like the best match for your use case. Would you like to use it, or create a new tailored plan instead?

**If the recommended plan is not a strong fit** (derive the gap in plain terms from `planSummary` and the criteria — e.g., infrequent cadence, single copy, missing retention info):

> **Choose Plan**
> Before we continue, here's a quick primer on what a backup plan is and what you'll be agreeing to — this is all standard Commvault terminology, so don't worry if it's new to you.
>
> A **backup plan** is the rulebook for protecting your data: it defines how often backups run (the RPO), how many independent copies are kept for safety, and how long those copies are retained before being automatically deleted.
>
> The closest available plan is **{planName}**:
> - **Backup frequency (RPO):** {rpoLabel} — {plain-language explanation of what this RPO means for their data risk}
> - **Copies:** {numCopies} — {numCopies} independent {copy/copies} of each backup {is/are} stored
> - **Summary:** {planSummary}
>
> It may not be the ideal fit: {plain-language reason — e.g. "it only keeps one copy, so a storage failure could leave you without a backup" or "backups run weekly, meaning up to a week of changes could be lost in a worst-case scenario"}. Would you like to use it anyway, or let me create a new plan optimised for daily backups, 2 copies, and 30-day retention?

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
> I wasn't able to create the storage pool. This can happen if a storage pool named **{storageName}** already exists. Please check the Commvault console and let me know to retry (or confirm it's safe to proceed with a different name).

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

On failure, silently call `list_eligible_plans` and scan the results for a plan whose name matches the one just attempted.

If a name match is found:

> **Create Plan — Needs attention**
> A plan named **{planName}** already exists. Would you like to use that plan instead, or choose a different name for the new one?

- User says use existing → retain that plan's `planId` and `planName` internally and continue to Step 9.
- User wants a different name → ask "What name would you like?" and retry `create_server_plan` with the new name, reusing the storage pool already created in Step 8c.

If no name match is found:

> **Create Plan — Needs attention**
> The plan could not be created. Note: the storage pool **{storageName}** was created and will need to be deleted in the Commvault console if you do not retry.

---

### Step 9 — Name and create the protection group

Derive a descriptive, human-readable name for the protection group from the conversation context — draw on the same signals used for the connection name (organisation or company name, account purpose, environment, etc.) and append something that conveys "protection" naturally (e.g. "acme-aws-backup", "prod-aws-protection", "marketing-cloud-pg"). The name should be unique and meaningful without relying on a rigid template.

Present the derived name for a simple confirmation. Briefly remind the user what this group will do:

> I'll create a protection group named **{derivedName}**. This group will cover all AWS workloads in your connection and back them up using the **{planName}** plan. Proceed?

- **Yes** → use the derived name.
- **No** → ask "What name would you like?" and use the answer.

Call `create_aws_protection_group` with the confirmed name, passing `workloads_json = "[]"` and `all_cloud_accounts = true` so Commvault protects everything in the connection automatically.

On success, say:

> **Create Protection Group — Done**
> The protection group **{name}** is now active and protecting your AWS workloads.

Retain the protection group ID from the response internally. Continue immediately to Step 10 — do not ask the user if they want to start a backup.

On failure:

> **Create Protection Group — Needs attention**
> The protection group could not be created. This can happen if a protection group named **{name}** already exists. Please check the Commvault console and, if it does, let me know to continue with the existing group or choose a different name and retry.

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

---

## Cleanup / Teardown

### When to Use

Trigger this flow whenever the user says anything that means tearing down the demo or starting over, including but not limited to:

- "clean the setup", "clean demo", "clean aws", "cleanup", "clean up"
- "tear down", "teardown", "remove aws", "delete aws setup"
- "reset", "reset the demo", "start fresh", "undo onboarding", "wipe aws"

### Opening message

Call `list_aws_cloud_connections` immediately (no user input needed). Then send this opening message, substituting the actual connection name(s):

> **Clean Up AWS — Working…**
> I'll clean up everything. Here's what I'll do:
> 1. **Delete the Commvault cloud connection** (and its protection group) — I found **{connectionName}** in Commvault.
> 2. **Delete the AWS CloudFormation stack and StackSet** — I'll show you the exact commands and run them once you confirm.
>
> Both steps are irreversible. Want me to proceed?

Wait for user confirmation before doing anything else. If the user confirms, proceed with Cleanup Step 1 immediately.

---

### Cleanup Step 1 — Delete the Commvault cloud connection

If `list_aws_cloud_connections` returned more than one connection, list them and ask which to delete before proceeding. For a single connection, use it directly.

Call `delete_aws_cloud_connection` with the connection's `id` and `name`.

On success:

> **Clean Up AWS — Done**
> Cloud connection **{connectionName}** and its protection group have been removed from Commvault.

On failure, report the error in plain language and ask the user whether to skip this step and continue with the AWS CLI cleanup or stop entirely. Do not silently skip.

If no connections exist, note that and proceed directly to Cleanup Step 2.

---

### Cleanup Step 2 — Delete AWS CloudFormation resources

Call `cleanup_aws_demo_environment` (no arguments needed for the default demo setup). The tool returns three ordered stages of AWS CLI commands.

Present **all commands** to the user before running anything:

> **Clean Up AWS — Needs confirmation**
> The following AWS CLI commands will permanently delete these resources:
> - CloudFormation stack: **CommvaultPermissionsStack**
> - StackSet instances in OU **{targetOuId}**: **CommvaultMemberAccountDiscovery**
> - StackSet definition: **CommvaultMemberAccountDiscovery**
>
> **Stage 1 — Delete access-role stack:**
> ```
> {stage1.commands[0].command}
> {stage1.commands[1].command}
> ```
>
> **Stage 2 — Delete StackSet instances:**
> ```
> {stage2.commands[0].command}
> {stage2.commands[1].command}
> {stage2.commands[2].command}
> ```
>
> **Stage 3 — Delete StackSet definition:**
> ```
> {stage3.commands[0].command}
> ```
>
> Confirm to run these now.

After explicit user confirmation, execute the stages in order:

**Stage 1:** Run the `delete-stack` command, then run `wait stack-delete-complete`. Show progress:
> ⏳ Deleting access-role stack…

On completion:
> **Clean Up AWS — Stage 1 Done**
> CommvaultPermissionsStack has been deleted.

**Stage 2:** Run `delete-stack-instances`. Parse the `OperationId` from the JSON output. Then poll `describe-stack-set-operation` with that `OperationId` every 15 seconds until the status is `SUCCEEDED` or `FAILED`. Show progress:
> ⏳ Removing StackSet instances from member accounts… (status: {current status})

If `SUCCEEDED`, run `list-stack-instances` to verify zero instances remain (the command returns a count).

If the count is `0`:
> **Clean Up AWS — Stage 2 Done**
> All StackSet instances have been removed from OU **{targetOuId}**.

If the count is non-zero or the operation `FAILED`, stop and report:
> **Clean Up AWS — Needs attention**
> Some StackSet instances could not be removed. Please check the AWS console for details. I will not delete the StackSet definition until all instances are gone.

If the StackSet was not found (error contains `StackSetNotFoundException`), treat that as already cleaned up and note it, then skip to the summary.

**Stage 3:** Only execute if stage 2 confirmed zero instances. Run `delete-stack-set`.

On success:
> **Clean Up AWS — Done**
> CommvaultMemberAccountDiscovery StackSet has been deleted.

---

### Cleanup Summary

After all stages complete:

> **Clean Up AWS — Done**
> The Commvault AWS demo environment has been fully removed:
> - Commvault cloud connection and protection group: removed
> - CloudFormation stack CommvaultPermissionsStack: deleted
> - StackSet CommvaultMemberAccountDiscovery: deleted from OU **{targetOuId}**
>
> You can run the onboarding flow again at any time to start fresh.
