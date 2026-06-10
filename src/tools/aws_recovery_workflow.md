# AWS Workload Recovery

## When to Use
- User wants to restore or recover any AWS workload — EC2 instances, RDS databases, S3 data, EBS volumes, or other AWS resources
- User mentions "restore", "recover", "in-place restore", or "undo changes" in an AWS context
- User wants to find a backed-up AWS workload and restore it
- User asks to restore a specific named AWS resource

## Tools Available (via MCP)
- `list_aws_virtual_machines` — lists all AWS workloads known to Commvault with their names and UUIDs
- `restore_aws_virtual_machine` — triggers an in-place restore for a given workload UUID and returns a job tracking URL

---

## User Communication Style

**Follow these rules for every message to the user throughout this flow:**

1. **Product-first language.** Use milestone names: `Find Workload`, `Restore Workload`, `Track Restore`. Never say "call the API" or use tool names in user-facing messages.

2. **Never expose raw data.** Do not show UUIDs, instance IDs, JSON, or internal IDs to the user. Match names to UUIDs internally.

3. **One question at a time.** After listing workloads, ask for exactly one name. Do not ask for the UUID.

4. **One status format.** After each action use exactly `**{Stage} — {State}**` on its own line, optionally followed by one short sentence. Example:
   > **Restore Workload — Working…**
   > Submitting the restore job for your selected resource.

   Stages: `Find Workload`, `Restore Workload`, `Track Restore`. States: `Working…`, `Done`, `Needs attention`.

5. **Plain-language errors.** On failure, say what the user should check in plain terms — no stack traces or raw error strings.

---

## Procedure

### Kickoff

When the user triggers this flow, send this opening message before doing anything:

> **Find Workload — Working…**
> I'll pull up the list of available AWS workloads so you can choose which one to restore.

Then proceed immediately to Step 1.

---

### Step 1 — List available workloads

Call `list_aws_virtual_machines`.

Present the workload names as a numbered list (names only — no UUIDs). Then ask:

> Which workload would you like to restore? You can give me the full name or just enough to identify it.

Retain the full `{name → UUID}` mapping internally for Step 2.

---

### Step 2 — Confirm and restore

Once the user provides a name:

1. Find the matching workload in the list (case-insensitive). If there is no exact match, identify the closest candidate and confirm with the user before proceeding.
2. Send this message:
   > **Restore Workload — Working…**
   > Triggering an in-place restore for **{workload_name}**. This will overwrite the existing resource.
3. Call `restore_aws_virtual_machine` with the resolved UUID.

---

### Step 3 — Report the job URL

On success, send:

> **Restore Workload — Done**
> The restore job has been submitted. You can track its progress here:
> {jobUrl}

On failure, say:

> **Restore Workload — Needs attention**
> The restore could not be started. Please check that the workload has a recent backup and try again. If the problem persists, contact support.
