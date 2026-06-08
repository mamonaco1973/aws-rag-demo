# ================================================================================
# Frontend S3 bucket base name
# ================================================================================

variable "frontend_bucket_base_name" {
  description = "Base name for the frontend S3 bucket"
  type        = string
  default     = "rag-app"
}

# ================================================================================
# Backend S3 bucket base name
# ================================================================================

variable "backend_bucket_base_name" {
  description = "Base name for the backend S3 bucket"
  type        = string
  default     = "rag-data"
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
# Bedrock model — Haiku for low-cost stateful RAG responses
# ================================================================================

variable "bedrock_model_id" {
  description = "Bedrock model ID used by the worker Lambda for RAG answers"
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}
