#Requires -Version 5.1
<#
.SYNOPSIS
    Deploys the LangGraph agent to Databricks Model Serving (Windows PowerShell).

.DESCRIPTION
    Uses the Databricks CLI + a Python helper (deploy_mlflow_helper.py) for
    MLflow model logging and Unity Catalog registration.
    Steps: resolve user -> log model -> register in Unity Catalog -> create serving endpoint.

.PARAMETER ModelName
    Unity Catalog model path (default: main.default.cs4603_langgraph_agent)

.PARAMETER EndpointName
    Serving endpoint name (default: cs4603-langgraph-agent)

.PARAMETER SkipEndpoint
    Skip creating/updating the serving endpoint (just log + register).

.EXAMPLE
    .\wk5_langgraph\15.databricks_deployment\deploy_setup.ps1
    .\wk5_langgraph\15.databricks_deployment\deploy_setup.ps1 -ModelName main.default.my_agent
    .\wk5_langgraph\15.databricks_deployment\deploy_setup.ps1 -SkipEndpoint
#>

param(
    [string]$ModelName = "main.default.cs4603_langgraph_agent",
    [string]$EndpointName = "cs4603-langgraph-agent",
    [switch]$SkipEndpoint
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$HelperScript = Join-Path $ScriptDir "deploy_mlflow_helper.py"

# ---- Load .env ---------------------------------------------------------------
$EnvFile = Join-Path $RepoRoot ".env"
if (Test-Path $EnvFile) {
    Write-Host "  Loading .env from $EnvFile"
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $eqIdx = $line.IndexOf("=")
            if ($eqIdx -gt 0) {
                $key = $line.Substring(0, $eqIdx).Trim()
                $val = $line.Substring($eqIdx + 1).Trim().Trim('"').Trim("'")
                [Environment]::SetEnvironmentVariable($key, $val, "Process")
            }
        }
    }
} else {
    Write-Warning "No .env file found at $EnvFile -- relying on existing env vars"
}

# ---- Validate required env vars ----------------------------------------------
$DATABRICKS_HOST = $env:DATABRICKS_HOST
$DATABRICKS_TOKEN = $env:DATABRICKS_TOKEN
$DATABRICKS_MODEL = if ($env:DATABRICKS_MODEL) { $env:DATABRICKS_MODEL } else { "databricks-qwen35-122b-a10b" }

if (-not $DATABRICKS_HOST) { Write-Error "DATABRICKS_HOST not set. Create a .env file at the repo root."; exit 1 }
if (-not $DATABRICKS_TOKEN) { Write-Error "DATABRICKS_TOKEN not set. Create a .env file at the repo root."; exit 1 }

# Force the Databricks CLI to use token auth (overrides any OAuth profile in ~/.databrickscfg)
$env:DATABRICKS_HOST = $DATABRICKS_HOST
$env:DATABRICKS_TOKEN = $DATABRICKS_TOKEN
$env:DATABRICKS_AUTH_TYPE = "pat"
# Prevent MLflow emoji output from crashing on Windows cp1252 console
$env:PYTHONIOENCODING = "utf-8"

Write-Host "============================================================"
Write-Host "  LangGraph Agent -- Databricks CLI Deployment (PowerShell)"
Write-Host "============================================================"
Write-Host "  Host:     $DATABRICKS_HOST"
Write-Host "  Model EP: $DATABRICKS_MODEL"
Write-Host "  UC Model: $ModelName"
Write-Host "  Endpoint: $EndpointName"
Write-Host ""

# ---- Step 1: Resolve Databricks username -------------------------------------
Write-Host "------------------------------------------------------------"
Write-Host "  Step 1: Resolve workspace username"
Write-Host "------------------------------------------------------------"

$userJson = databricks current-user me --output json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "  'databricks current-user me' failed. Check CLI auth.`n$userJson"
    exit 1
}

$userObj = $userJson | ConvertFrom-Json
$DbUser = $userObj.userName
Write-Host "  [OK] User: $DbUser"

$ExperimentPath = "/Users/$DbUser/wk5-deployment"
Write-Host "  Experiment: $ExperimentPath"

# ---- Step 2: Log model to MLflow ---------------------------------------------
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host "  Step 2: Log agent model to MLflow"
Write-Host "------------------------------------------------------------"

$ModelCodePath = Join-Path $ScriptDir "agent.py"
Write-Host "  Model code: $ModelCodePath"

$logOutput = python $HelperScript log `
    --host $DATABRICKS_HOST `
    --token $DATABRICKS_TOKEN `
    --model $DATABRICKS_MODEL `
    --experiment $ExperimentPath `
    --code $ModelCodePath 2>&1

$runIdLine = ($logOutput | Select-String "__RUN_ID__=").Line
$modelUriLine = ($logOutput | Select-String "__MODEL_URI__=").Line

if (-not $runIdLine -or -not $modelUriLine) {
    Write-Error "  MLflow model logging failed:`n$logOutput"
    exit 1
}

$RunId = ($runIdLine -split "=", 2)[1].Trim()
$ModelUri = ($modelUriLine -split "=", 2)[1].Trim()

Write-Host "  [OK] Run ID:    $RunId"
Write-Host "  [OK] Model URI: $ModelUri"

# ---- Step 3: Register model in Unity Catalog ---------------------------------
Write-Host ""
Write-Host "------------------------------------------------------------"
Write-Host "  Step 3: Register model in Unity Catalog"
Write-Host "  Name: $ModelName"
Write-Host "------------------------------------------------------------"

$regOutput = python $HelperScript register `
    --host $DATABRICKS_HOST `
    --token $DATABRICKS_TOKEN `
    --model-uri $ModelUri `
    --model-name $ModelName 2>&1

$versionLine = ($regOutput | Select-String "__MODEL_VERSION__=").Line

if (-not $versionLine) {
    Write-Error "  Model registration failed:`n$regOutput"
    exit 1
}

$ModelVersion = ($versionLine -split "=", 2)[1].Trim()
Write-Host "  [OK] Registered version: $ModelVersion"

# ---- Step 4: Create / update serving endpoint --------------------------------
if ($SkipEndpoint) {
    Write-Host ""
    Write-Host "------------------------------------------------------------"
    Write-Host "  Step 4: Skipped (-SkipEndpoint)"
    Write-Host "------------------------------------------------------------"
} else {
    Write-Host ""
    Write-Host "------------------------------------------------------------"
    Write-Host "  Step 4: Create/update Model Serving endpoint"
    Write-Host "  Endpoint: $EndpointName"
    Write-Host "------------------------------------------------------------"

    $entityJson = @{
        name = $EndpointName
        config = @{
            served_entities = @(
                @{
                    entity_name = $ModelName
                    entity_version = "$ModelVersion"
                    workload_size = "Small"
                    scale_to_zero_enabled = $true
                    environment_vars = @{
                        DATABRICKS_HOST = $DATABRICKS_HOST
                        DATABRICKS_TOKEN = $DATABRICKS_TOKEN
                        DATABRICKS_MODEL = $DATABRICKS_MODEL
                    }
                }
            )
        }
    } | ConvertTo-Json -Depth 5 -Compress

    $updateJson = @{
        served_entities = @(
            @{
                entity_name = $ModelName
                entity_version = "$ModelVersion"
                workload_size = "Small"
                scale_to_zero_enabled = $true
                environment_vars = @{
                    DATABRICKS_HOST = $DATABRICKS_HOST
                    DATABRICKS_TOKEN = $DATABRICKS_TOKEN
                    DATABRICKS_MODEL = $DATABRICKS_MODEL
                }
            }
        )
    } | ConvertTo-Json -Depth 5 -Compress

    # Check if endpoint exists
    $null = databricks serving-endpoints get $EndpointName --output json 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Creating endpoint '$EndpointName'..."
        databricks serving-endpoints create --json $entityJson --no-wait
        if ($LASTEXITCODE -ne 0) { Write-Error "  Failed to create endpoint."; exit 1 }
        Write-Host "  [OK] Endpoint created (may take a few minutes to become READY)"
    } else {
        Write-Host "  Endpoint exists -- updating to version $ModelVersion..."
        databricks serving-endpoints update-config $EndpointName --json $updateJson --no-wait
        if ($LASTEXITCODE -ne 0) { Write-Error "  Failed to update endpoint."; exit 1 }
        Write-Host "  [OK] Endpoint update started (may take a few minutes to become READY)"
    }

    Write-Host "  Endpoint URL: $DATABRICKS_HOST/serving-endpoints/$EndpointName/invocations"
}

# ---- Summary -----------------------------------------------------------------
Write-Host ""
Write-Host "============================================================"
Write-Host "  Setup Complete!"
Write-Host "============================================================"
Write-Host ""
Write-Host "  Model:     $ModelName (version $ModelVersion)"
Write-Host "  Endpoint:  $EndpointName"
Write-Host "  Run ID:    $RunId"
Write-Host ""
Write-Host "  To check endpoint status:"
Write-Host "    databricks serving-endpoints get $EndpointName"
Write-Host ""
Write-Host "  To test (once READY):"
Write-Host "    python -c ""import openai; c=openai.OpenAI(api_key='TOKEN',base_url='$DATABRICKS_HOST/serving-endpoints'); print(c.chat.completions.create(model='$EndpointName',messages=[{'role':'user','content':'Convert 100F to Celsius'}]).choices[0].message.content)"""
Write-Host ""
