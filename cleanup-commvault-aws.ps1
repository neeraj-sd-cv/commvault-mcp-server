# cleanup-commvault-aws.ps1
# Run from the delegated admin account (aws configure / env vars must be set).
# Edit OU_ID and REGION to match your environment.

$OU_ID    = "ou-anxa-qikxlrp2"
$REGION   = "us-east-1"
$STACK    = "CommvaultPermissionsStack"
$STACKSET = "CommvaultMemberAccountDiscovery"
$CALL_AS  = "DELEGATED_ADMIN"
$DIV      = "=========================================="

Write-Host ""
Write-Host $DIV
Write-Host " Commvault AWS Cleanup Script"
Write-Host $DIV
Write-Host "  Stack    : $STACK"
Write-Host "  StackSet : $STACKSET"
Write-Host "  OU ID    : $OU_ID"
Write-Host "  Region   : $REGION"
Write-Host "  CallAs   : $CALL_AS"
Write-Host $DIV
Write-Host ""

# ---------------------------------------------------------------------------
Write-Host "--- Step 1: Delete access-role CFT stack ---"
Write-Host "  Stack    : $STACK"
Write-Host "  Region   : $REGION"
Write-Host "  Sending delete-stack request..."
aws cloudformation delete-stack --stack-name $STACK --region $REGION
Write-Host "  Waiting for DELETE_COMPLETE..."
aws cloudformation wait stack-delete-complete --stack-name $STACK --region $REGION
Write-Host "  [DONE] Stack '$STACK' deleted."

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Step 2: Delete StackSet instances from OU ---"
Write-Host "  StackSet : $STACKSET"
Write-Host "  OU ID    : $OU_ID"
Write-Host "  Region   : $REGION"
Write-Host "  CallAs   : $CALL_AS"
Write-Host "  Retain   : false (stacks in member accounts will be deleted)"
Write-Host "  Sending delete-stack-instances request..."

$canDeleteStackSet = $false
$stackSetAlreadyGone = $false

$deleteJson = aws cloudformation delete-stack-instances `
    --stack-set-name $STACKSET `
    --deployment-targets "OrganizationalUnitIds=$OU_ID" `
    --regions $REGION `
    --no-retain-stacks `
    --call-as $CALL_AS `
    --region $REGION `
    --output json 2>&1
$deleteExitCode = $LASTEXITCODE
Write-Host "  Response : $deleteJson"

if ($deleteExitCode -ne 0) {
    Write-Host "  [SKIP] delete-stack-instances failed (exit $deleteExitCode) - skipping poll."
    if ($deleteJson -match "StackSetNotFoundException") {
        Write-Host "  NOTE: StackSet '$STACKSET' was not found. Nothing to delete in member accounts."
        $stackSetAlreadyGone = $true
    } else {
        Write-Host "  ERROR: Member account stacks were not deleted. Not deleting the StackSet definition."
    }
} else {
    $deleteOpId = $null
    try {
        $deleteOpId = ($deleteJson | ConvertFrom-Json -ErrorAction Stop).OperationId
    } catch {
        Write-Host "  ERROR: Could not parse OperationId from delete-stack-instances response."
    }

    if (-not $deleteOpId) {
        Write-Host "  ERROR: No OperationId returned. Not deleting the StackSet definition."
    } else {
        Write-Host "  OperationId: $deleteOpId"
        Write-Host "  NOTE: AWS is now deleting stacks inside member accounts in OU '$OU_ID'."
        Write-Host "  Polling the delete operation every 15 s (max 10 min)..."

        $attempt = 0
        $terminalStatuses = @("SUCCEEDED", "FAILED", "STOPPED")
        $opStatus = $null

        do {
            $opStatus = aws cloudformation describe-stack-set-operation `
                --stack-set-name $STACKSET `
                --operation-id $deleteOpId `
                --call-as $CALL_AS `
                --region $REGION `
                --query "StackSetOperation.Status" `
                --output text 2>&1
            $opExitCode = $LASTEXITCODE
            $attempt++

            if ($opExitCode -ne 0) {
                Write-Host "  [attempt $attempt] ERROR polling operation: $opStatus"
                break
            }

            Write-Host "  [attempt $attempt] Delete operation status: $opStatus"

            if ($terminalStatuses -contains $opStatus) {
                break
            }

            Start-Sleep -Seconds 15
        } while ($attempt -lt 40)

        if ($opStatus -eq "SUCCEEDED") {
            Write-Host "  [DONE] Delete operation succeeded."

            Write-Host "  Verifying zero StackSet instances remain..."
            $remainingCount = aws cloudformation list-stack-instances `
                --stack-set-name $STACKSET `
                --call-as $CALL_AS `
                --region $REGION `
                --query "length(Summaries)" `
                --output text 2>&1
            $countExitCode = $LASTEXITCODE

            if ($countExitCode -ne 0) {
                Write-Host "  ERROR: Could not verify remaining instances: $remainingCount"
            } elseif ($remainingCount -eq "0") {
                Write-Host "  [DONE] 0 StackSet instances remain."
                $canDeleteStackSet = $true
            } else {
                Write-Host "  ERROR: $remainingCount StackSet instance(s) still remain. Not deleting StackSet definition."
            }
        } else {
            Write-Host "  ERROR: Delete operation did not succeed (status: $opStatus). Not deleting StackSet definition."
        }
    }
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Step 3: Delete StackSet definition ---"
Write-Host "  StackSet : $STACKSET"
Write-Host "  Region   : $REGION"
Write-Host "  CallAs   : $CALL_AS"

if ($stackSetAlreadyGone) {
    Write-Host "  [SKIP] StackSet '$STACKSET' is already gone."
} elseif (-not $canDeleteStackSet) {
    Write-Host "  [SKIP] StackSet instances are not confirmed deleted. Leaving StackSet definition in place."
} else {
    Write-Host "  Sending delete-stack-set request..."
    $ssResult = aws cloudformation delete-stack-set `
        --stack-set-name $STACKSET `
        --call-as $CALL_AS `
        --region $REGION 2>&1
    $ssExitCode = $LASTEXITCODE
    if ($ssExitCode -ne 0) {
        Write-Host "  [SKIP] delete-stack-set failed (exit $ssExitCode): $ssResult"
    } else {
        Write-Host "  [DONE] StackSet '$STACKSET' deleted."
    }
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host $DIV
Write-Host " Cleanup finished."
Write-Host "  Stack '$STACK' delete requested and waited."
Write-Host "  StackSet instances in OU '$OU_ID' deletion attempted."
Write-Host "  StackSet '$STACKSET' deletion attempted only after zero instances remained."
Write-Host $DIV
# cleanup-commvault-aws.ps1
# Run from the delegated admin account (aws configure / env vars must be set).
# Edit OU_ID and REGION to match your environment.

$OU_ID    = "ou-anxa-qikxlrp2"
$REGION   = "us-east-1"
$STACK    = "CommvaultPermissionsStack"
$STACKSET = "CommvaultMemberAccountDiscovery"
$DIV      = "=========================================="

Write-Host ""
Write-Host $DIV
Write-Host " Commvault AWS Cleanup Script"
Write-Host $DIV
Write-Host "  Stack    : $STACK"
Write-Host "  StackSet : $STACKSET"
Write-Host "  OU ID    : $OU_ID"
Write-Host "  Region   : $REGION"
Write-Host $DIV
Write-Host ""

# ---------------------------------------------------------------------------
Write-Host "--- Step 1: Delete access-role CFT stack ---"
Write-Host "  Stack    : $STACK"
Write-Host "  Region   : $REGION"
Write-Host "  Sending delete-stack request..."
aws cloudformation delete-stack --stack-name $STACK --region $REGION
Write-Host "  Waiting for DELETE_COMPLETE..."
aws cloudformation wait stack-delete-complete --stack-name $STACK --region $REGION
Write-Host "  [DONE] Stack '$STACK' deleted."

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Step 2: Delete StackSet instances from OU ---"
Write-Host "  StackSet : $STACKSET"
Write-Host "  OU ID    : $OU_ID"
Write-Host "  Region   : $REGION"
Write-Host "  Retain   : false (stacks in member accounts will be deleted)"
Write-Host "  Sending delete-stack-instances request..."
$deleteResult = aws cloudformation delete-stack-instances `
    --stack-set-name $STACKSET `
    --deployment-targets "OrganizationalUnitIds=$OU_ID" `
    --regions $REGION `
    --no-retain-stacks `
    --region $REGION `
    --output json 2>&1
$deleteExitCode = $LASTEXITCODE
Write-Host "  Response : $deleteResult"

if ($deleteExitCode -ne 0) {
    Write-Host "  [SKIP] delete-stack-instances failed (exit $deleteExitCode) - skipping poll."
    Write-Host "  NOTE: If the StackSet never existed, there are no member account stacks to clean up."
} else {
    Write-Host "  NOTE: AWS is now deleting the stacks inside each member account in OU '$OU_ID'."
    Write-Host "  Polling every 15 s until no RUNNING operations (max 10 min)..."
    $attempt = 0
    do {
        Start-Sleep -Seconds 15
        $runningOps = aws cloudformation list-stack-set-operations `
            --stack-set-name $STACKSET `
            --region $REGION `
            --query "Summaries[?Status=='RUNNING'].OperationId" `
            --output text 2>$null
        $attempt++
        if ($runningOps -and $runningOps -ne "") {
            Write-Host "  [attempt $attempt] Still running - op IDs: $runningOps"
        } else {
            Write-Host "  [attempt $attempt] No running operations."
            break
        }
    } while ($attempt -lt 40)
    Write-Host "  [DONE] Stack instances removed from OU '$OU_ID'."
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "--- Step 3: Delete StackSet definition ---"
Write-Host "  StackSet : $STACKSET"
Write-Host "  Region   : $REGION"
Write-Host "  Sending delete-stack-set request..."
$ssResult = aws cloudformation delete-stack-set --stack-set-name $STACKSET --region $REGION 2>&1
$ssExitCode = $LASTEXITCODE
if ($ssExitCode -ne 0) {
    Write-Host "  [SKIP] delete-stack-set failed (exit $ssExitCode): $ssResult"
} else {
    Write-Host "  [DONE] StackSet '$STACKSET' deleted."
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host $DIV
Write-Host " Cleanup complete. Ready for a fresh run."
Write-Host "  Stack '$STACK' deleted."
Write-Host "  StackSet instances in OU '$OU_ID' deleted."
Write-Host "  StackSet '$STACKSET' deleted."
Write-Host $DIV
