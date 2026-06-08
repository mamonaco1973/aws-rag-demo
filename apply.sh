#!/bin/bash
# ================================================================================
# File: apply.sh
# ================================================================================
#
# Purpose:
#   Orchestrates end-to-end deployment of the resume scorer application stack.
#
#   Workflow:
#     - Validate the local environment and AWS credentials
#     - Deploy backend (Lambdas, API Gateway, Cognito) via Terraform
#     - Discover the web S3 bucket and derive its region-aware URL
#     - Generate the web client artifacts (index.html, config.json)
#     - Deploy the web client via Terraform, targeting the existing bucket
#
# ================================================================================
# GLOBAL CONFIGURATION
# ================================================================================

# ------------------------------------------------------------------------------
# AWS REGION CONFIGURATION
# ------------------------------------------------------------------------------
# Defines the default AWS region used by AWS CLI and Terraform.
# ------------------------------------------------------------------------------
export AWS_DEFAULT_REGION="us-east-1"

# ------------------------------------------------------------------------------
# BEDROCK MODEL CONFIGURATION
# ------------------------------------------------------------------------------
# Exports BEDROCK_MODEL_ID so check_env.sh probes the correct model.
# Also passed to Terraform so the worker Lambda env var stays in sync.
# ------------------------------------------------------------------------------
source "$(dirname "$0")/bedrock-config.sh"

# ------------------------------------------------------------------------------
# STRICT SHELL EXECUTION MODE
# ------------------------------------------------------------------------------
# Enforces defensive shell behavior:
#   -e  Exit immediately if any command fails
#   -u  Treat unset variables as errors
#   -o pipefail  Fail pipelines if any command fails
# ------------------------------------------------------------------------------
set -euo pipefail

# ================================================================================
# ENVIRONMENT PRE-CHECK
# ================================================================================

# ------------------------------------------------------------------------------
# ENVIRONMENT VALIDATION
# ------------------------------------------------------------------------------
# Ensures required tools, credentials, and environment variables exist
# before any deployment is attempted.
# ------------------------------------------------------------------------------
echo "NOTE: Running environment validation..."

./check_env.sh
if [ $? -ne 0 ]; then
  echo "ERROR: Environment validation failed. Exiting."
  exit 1
fi

# ================================================================================
# BACKEND DEPLOYMENT (LAMBDAS + API GATEWAY + COGNITO)
# ================================================================================

# ------------------------------------------------------------------------------
# DEPLOY BACKEND INFRASTRUCTURE
# ------------------------------------------------------------------------------
# Applies Terraform in 01-lambdas to create the backend stack, including:
#   - Lambda functions
#   - API Gateway (HTTP API)
#   - Cognito (domain + app client outputs are read later)
# ------------------------------------------------------------------------------
echo "NOTE: Building Application Core Services..."

cd 01-core || {
  echo "ERROR: 01-core directory missing."
  exit 1
}

cd code
pip install -r requirements.txt -t .
cd ..

terraform init
terraform apply -auto-approve -var="bedrock_model_id=${BEDROCK_MODEL_ID}"

export API_BASE_URL=$(terraform output -raw api_endpoint)
export BUCKET_NAME=$(terraform output -raw frontend_bucket_name)
export BUCKET_URL=$(terraform output -raw frontend_website_url)
export COGNITO_DOMAIN=$(terraform output -raw cognito_hosted_ui_base)
export COGNITO_CLIENT_ID=$(terraform output -raw cognito_user_pool_client_id)

cd .. || exit 1

# ------------------------------------------------------------------------------
# DEPLOYING WEB CLIENT ARTIFACTS
# ------------------------------------------------------------------------------
echo "NOTE: Deploying web application..."

cd 02-webapp || {
  echo "ERROR: 02-webapp directory missing."
  exit 1
}

envsubst < js/config.js.tmpl > js/config.js || {
  echo "ERROR: Failed to generate config.js."
  exit 1
}

aws s3 cp . s3://${BUCKET_NAME} --recursive

cd ..

# ------------------------------------------------------------------------------
# RUNTIME VALIDATION
# ------------------------------------------------------------------------------
echo "NOTE: Running post-deployment validation..."
./validate.sh

# ================================================================================
# END OF SCRIPT
# ================================================================================