#!/bin/bash
# ================================================================================
# apply.sh
# Full deployment of the RAG demo application stack.
#
# Workflow:
#   1. Validate environment and AWS credentials
#   2. Install Python deps into the Lambda source directory
#   3. Deploy backend (Lambdas, API Gateway, Cognito, SQS, DynamoDB, S3)
#   4. Generate config.js for the SPA
#   5. Upload the SPA to S3
#   6. Build and upload RAG corpus (GitHub + YouTube → Bedrock Titan → S3)
#   7. Run post-deploy validation
# ================================================================================

# ================================================================================
# Global configuration
# ================================================================================

export AWS_DEFAULT_REGION="us-east-1"

# Exports BEDROCK_MODEL_ID — sourced before set -euo pipefail so the file can
# use simple variable assignments without tripping the unbound-variable check.
source "$(dirname "$0")/bedrock-config.sh"

set -euo pipefail

# ================================================================================
# Environment pre-check
# ================================================================================

echo "NOTE: Running environment validation..."

./check_env.sh
if [ $? -ne 0 ]; then
  echo "ERROR: Environment validation failed. Exiting."
  exit 1
fi

# ================================================================================
# Backend deployment
# ================================================================================

echo "NOTE: Deploying backend infrastructure..."

cd 01-core || { echo "ERROR: 01-core directory missing."; exit 1; }

# ------------------------------------------------------------------------------
# Install Lambda dependencies into the code directory so they are included
# in the zip archive that Terraform packages and deploys.
# ------------------------------------------------------------------------------
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

# ================================================================================
# Frontend deployment
# ================================================================================

echo "NOTE: Deploying web application..."

cd 02-webapp || { echo "ERROR: 02-webapp directory missing."; exit 1; }

envsubst < js/config.js.tmpl > js/config.js || {
  echo "ERROR: Failed to generate config.js."
  exit 1
}

aws s3 cp . s3://${BUCKET_NAME} --recursive --exclude "*.tmpl"

cd ..

# ================================================================================
# Corpus ingestion
# ================================================================================

echo "NOTE: Checking corpus ingestion..."

export BACKEND_BUCKET=$(cd 01-core && terraform output -raw backend_bucket_name)

if aws s3api head-object --bucket "${BACKEND_BUCKET}" --key "corpus/chunks.json" &>/dev/null; then
  echo "NOTE: Corpus already ingested — skipping."
else
  echo "NOTE: Corpus not found — running ingestion..."
  cd 03-ingest || { echo "ERROR: 03-ingest directory missing."; exit 1; }
  pip3 install -r requirements.txt -q
  python3 ingest.py --bucket "${BACKEND_BUCKET}" --region "${AWS_DEFAULT_REGION}"
  cd ..
fi

# ================================================================================
# Post-deploy validation
# ================================================================================

echo "NOTE: Running post-deployment validation..."
./validate.sh

# ================================================================================
# End
# ================================================================================
