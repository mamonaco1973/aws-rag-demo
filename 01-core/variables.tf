# =================================================================================
# Frontend S3 bucket base name
# Actual bucket name will be <base>-<random_id>
# =================================================================================

variable "frontend_bucket_base_name" {
  description = "Base name for the frontend S3 bucket"
  type        = string
  default     = "resume-app"
}

# =================================================================================
# Backend S3 bucket base name
# Actual bucket name will be <base>-<random_id>
# =================================================================================

variable "backend_bucket_base_name" {
  description = "Base name for the backend S3 bucket"
  type        = string
  default     = "resume-data"
}

# ================================================================================
# AWS region
# ================================================================================

variable "region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

# ================================================================================
# Bedrock model configuration
# ================================================================================

variable "bedrock_model_id" {
  description = "Bedrock model ID used by worker Lambda for job extraction"
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

