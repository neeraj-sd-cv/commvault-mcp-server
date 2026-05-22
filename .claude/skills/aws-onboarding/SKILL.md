---
name: aws-onboarding
description: 'Guide the user through onboarding an AWS account or AWS Organization to Commvault, and optionally setting up a protection group. Use when the user says: onboard AWS, set up AWS cloud connection, connect AWS, add AWS account, AWS onboarding, set up protection group, protect AWS workloads.'
argument-hint: 'delegated admin AWS account ID (optional)'
---

# AWS Cloud Onboarding and Protection Group Setup

## When to Use
- User wants to onboard AWS or add an AWS cloud connection
- User mentions delegated admin account, AWS Organization, or AWS CFT permissions
- User wants to set up a protection group after onboarding

## Tools Available (via MCP)
- `get_aws_permissions_cft` — fetches CFT quick-create URLs and IAM role details
- `validate_aws_cloud_credentials` — validates Commvault can assume the IAM role
- `browse_aws_cloud_accounts` — confirms account discovery through the IAM role
- `create_aws_cloud_connection` — creates the final cloud connection
- `list_aws_cloud_connections` — lists existing AWS cloud connections to select from
- `list_aws_workloads` — lists available AWS workload types grouped by category
- `list_eligible_plans` — lists backup plans eligible for an AWS protection group
- `create_aws_protection_group` — creates the protection group
- `start_aws_protection_group_backup` — starts an initial backup for the created protection group

---

## User Communication Style

**Follow these rules for every message to the user throughout this flow:**

1. **Product-first language.** Use milestone names: `Connect AWS`, `Validate Access`, `Discover Accounts`, `Create Connection`, `Choose Workloads`, `Choose Plan`, `Create Protection Group`, `Start Backup`. Never say "call the API" or use tool names in user-facing messages.

2. **Never expose raw data.** Do not paste JSON, API response objects, internal IDs, ARNs, or payload fields into user messages. Retain them internally for subsequent tool calls.

3. **One question at a time.** After each action, present one clear ask or confirmation. Do not bundle multiple questions.

4. **Compact status format.** After each action use this pattern:
   - **Done:** what was just completed or verified.
   - **Next:** the single action the user needs to take, or the question you need answered.

5. **Show IDs only when needed.** Show a connection or plan ID only if two items share the same display name. Otherwise use names only.

6. **Plain-language errors.** On failure, say what the user should check in plain terms. No stack traces or raw error strings.

---

## Procedure

### Step 1 — Get the account ID

If the user has not provided it, ask:

> To get started, I need your 12-digit AWS delegated admin account ID. What is it?

---

### Step 2 — Deploy the Commvault access role (CFT)

Call `get_aws_permissions_cft` with the account ID.

From the response, extract `connectionTypes.organization.cftQuickCreateUrl`.

Say to the user:

> **Connect AWS — Action required in your AWS Console**
>
> Please open the link below to deploy a CloudFormation stack. This creates the IAM role that lets Commvault access your AWS environment.
>
> [Deploy CloudFormation Stack]({cftQuickCreateUrl})
>
> Once the stack status shows **CREATE_COMPLETE**, let me know and I'll continue.

Wait for the user to confirm before continuing.

---

### Step 3 — Validate AWS access

Call `validate_aws_cloud_credentials` with the account ID and connection type `organization`.

On success, say:

> **Validate Access — Done**
> Commvault can now access your AWS account. Let's set up member account discovery.

On failure, say:

> **Validate Access — Needs attention**
> Commvault could not access your account yet. Please check that the CloudFormation stack status is **CREATE_COMPLETE** in your AWS Console, then let me know and I'll retry.

---

### Step 4 — Set up member account discovery (StackSet)

Use the `memberAccountSetup` fields from the Step 2 response. Do not show the raw values as JSON. Present only what the user needs to act on:

> **Discover Accounts — Action required in your AWS Console**
>
> To allow Commvault to discover and protect resources in your member accounts, deploy a CloudFormation StackSet from your delegated admin account.
>
> 1. Open: https://console.aws.amazon.com/cloudformation/home#/stacksets/create
> 2. Under **Specify template**, choose **Amazon S3 URL** and paste:
>    `{memberAccountSetup.templateUrl}`
> 3. Under **Parameters**, enter:
>    - **Commvault Cloud IAM Role ARN:** `{memberAccountSetup.hostedInfraRoleArn}`
>    - **Commvault Cloud IAM User ARN:** `{memberAccountSetup.hostedInfraUserArn}`
> 4. Accept the defaults and submit the StackSet.
>
> Once the StackSet is deployed across your member accounts, let me know.

Wait for user confirmation before continuing.

---

### Step 5 — Verify account discovery

Call `browse_aws_cloud_accounts` with the account ID.

On success (accounts returned), summarize concisely — show the count and a short sample, not the full list:

> **Discover Accounts — Done**
> Commvault discovered {N} AWS accounts. Ready to create the cloud connection.

If zero accounts are returned:

> **Discover Accounts — Needs attention**
> No accounts were found. Please verify the StackSet deployed successfully in all target member accounts, then let me know and I'll retry.

---

### Step 6 — Name and create the cloud connection

Ask the user:

> What would you like to name this AWS cloud connection?

Call `create_aws_cloud_connection` with the connection name and account ID.

On success, use the `summary` from the response to compose the message. Show the job link only if `summary.discoveryJobUrl` is present.

> **Create Connection — Done**
> The cloud connection **{summary.connectionName}** has been created and account discovery has started. Commvault will begin discovering resources across your member accounts.
>
> You can track the discovery job here: [{summary.discoveryJobUrl}]({summary.discoveryJobUrl})
>
> Would you like to set up a protection group now?

If `summary.discoveryJobUrl` is absent (API did not return a job ID), omit the tracking link:

> **Create Connection — Done**
> The cloud connection **{summary.connectionName}** has been created and account discovery has started.
>
> Would you like to set up a protection group now?

Immediately continue to the protection group steps once the user confirms — do not wait for discovery to finish.

---

## Protection Group Setup (Steps 7–11)

If the user says yes, continue. The connection from Step 6 is already available — do not call `list_aws_cloud_connections` or ask the user to pick a connection again.

### Step 7 — Choose workloads

Call `list_aws_workloads`. Present the results grouped by category. Do not show IDs.

> **Choose Workloads**
> Which AWS workloads should Commvault protect? You can choose any combination.
>
> **File Servers**
> - Amazon Elastic File System
>
> **Virtualization**
> - Amazon EC2
>
> **Databases**
> - Amazon Aurora and RDS Snapshot
> - Amazon DynamoDB
> - Amazon Redshift
> _(and so on for all categories)_

Wait for the user's selection. Build the workloads JSON array internally from their choices. Do not show the JSON to the user.

---

### Step 8 — Choose a backup plan

Call `list_eligible_plans`. Present each plan as name and a human-readable summary. Do not show raw plan IDs or the full planSummary string.

Format the RPO from `rpoInMinutes`:
- 0 → `No scheduled RPO`
- < 60 → `Every {N} minutes`
- < 1440 → `Every {H}h {M}m`
- 1440 → `Daily`
- 10080 → `Weekly`
- 43800 → `Monthly`
- ≥ 525600 → `Yearly`

> **Choose Plan**
> Which backup plan would you like to use?
>
> - **{planName}** — {rpoLabel}, {numCopies} copies
> - **{planName}** — {rpoLabel}, {numCopies} copies

Wait for the user's choice. Retain the `planId` internally.

---

### Step 9 — Name and create the protection group

Ask the user:

> What would you like to name this protection group?

Before calling the tool, show a confirmation summary:

> Here's what I'll set up:
> - **Connection:** {connection_name}
> - **Workloads:** {comma-separated list of selected workload names}
> - **Plan:** {planName}
> - **Protection group name:** {name}
>
> Shall I go ahead?

Call `create_aws_protection_group` only after the user confirms.

On success, say:

> **Create Protection Group — Done**
> The protection group **{name}** is now active and protecting your selected workloads.
>
> Would you like to start the initial full backup now?

Retain the protection group ID from the response internally.

---

### Step 10 — Start the initial backup

If the user says yes, call `start_aws_protection_group_backup` with:
- `protection_group_id` — the created protection group's ID (from Step 9 response)
- `backup_level` — `FULL`
- `notify_user_on_job_completion` — `true`
- `job_description` — `Initial full after user onboarding`

On success, use `summary.jobUrl` from the response. Show the link if present.

> **Start Backup — Done**
> The initial full backup job has been submitted. You'll receive a notification when it completes.
>
> Track the job here: [{summary.jobUrl}]({summary.jobUrl})
>
> Your AWS environment is now fully onboarded and protected by Commvault.

If `summary.jobUrl` is absent, omit the tracking link:

> **Start Backup — Done**
> The initial full backup job has been submitted. You'll receive a notification when it completes.
>
> Your AWS environment is now fully onboarded and protected by Commvault.
