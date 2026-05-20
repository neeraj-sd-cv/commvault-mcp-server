---
name: aws-onboarding
description: 'Guide the user through onboarding an AWS account or AWS Organization to Commvault. Use when the user says: onboard AWS, set up AWS cloud connection, connect AWS, add AWS account, AWS onboarding.'
argument-hint: 'delegated admin AWS account ID (optional)'
---

# AWS Cloud Onboarding

## When to Use
- User wants to onboard AWS or add an AWS cloud connection
- User mentions delegated admin account, AWS Organization, or AWS CFT permissions

## Tools Available (via MCP)
- `get_aws_permissions_cft` — fetches CFT quick-create URLs and IAM role details
- `validate_aws_cloud_credentials` — validates Commvault can assume the IAM role
- `browse_aws_cloud_accounts` — confirms account discovery through the IAM role
- `create_aws_cloud_connection` — creates the final cloud connection

## Procedure

### Step 1 — Get the delegated admin account ID
If the user did not provide it, ask:
> "What is the AWS account ID of your delegated admin account?"

### Step 2 — Retrieve CFT links
Call `get_aws_permissions_cft` with the account ID.

From the response (`connectionTypes.organization.cftQuickCreateUrl`), present the URL:
> "Please open this link in your AWS Console to deploy the CloudFormation stack — it creates the required IAM role for Commvault:
> [Deploy CloudFormation Stack]({cftQuickCreateUrl})"

Wait for the user to confirm the stack deployed successfully before continuing.

### Step 3 — Validate credentials
Call `validate_aws_cloud_credentials` with the account ID.

If it fails, tell the user:
> "Validation failed — please check that the CloudFormation stack status is CREATE_COMPLETE in your AWS Console, then we can retry."

### Step 4 — Member account StackSet instructions
Use the `memberAccountSetup` fields from the Step 2 response to present these instructions:

---
**Configure IAM role in Member AWS Accounts**

Deploy a CloudFormation StackSet from the delegated admin account to allow resource discovery and backup in member accounts.

1. Sign in to the delegated admin account and open:
   https://console.aws.amazon.com/cloudformation/home#/stacksets/create
2. In **Specify template**, paste this S3 URL:
   `{connectionTypes.organization.memberAccountSetup.templateUrl}`
3. In **Parameters**, enter:
   - Commvault Cloud IAM Role ARN: `{connectionTypes.organization.memberAccountSetup.hostedInfraRoleArn}`
   - Commvault Cloud IAM User ARN: `{connectionTypes.organization.memberAccountSetup.hostedInfraUserArn}`
4. Proceed with the default options and submit.

---

Ask the user: "Have you successfully deployed the StackSet across all member accounts?"

### Step 5 — Browse and verify accounts
Call `browse_aws_cloud_accounts` with the account ID.

Show the user the discovered accounts. If none are returned:
> "No accounts were discovered. Please verify the StackSet deployed successfully in all target member accounts, then we can retry."

### Step 6 — Create the connection
Ask the user:
> "What would you like to name this AWS cloud connection?"

Call `create_aws_cloud_connection` with the connection name and account ID.

Confirm success:
> "Your AWS cloud connection has been created and a discovery job has started. Commvault will begin discovering and protecting resources across your member accounts."
