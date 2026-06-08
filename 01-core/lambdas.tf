# ================================================================================
# Lambda function
# ================================================================================

resource "aws_lambda_function" "api" {

  function_name = "resume-api-${random_id.bucket_suffix.hex}"

  filename         = data.archive_file.lambdas_zip.output_path
  source_code_hash = data.archive_file.lambdas_zip.output_base64sha256

  handler = "handler.lambda_handler"
  runtime = "python3.11"

  role = aws_iam_role.lambda_exec.arn

  timeout = 10

  environment {
    variables = {
      TABLE_NAME          = aws_dynamodb_table.app_table.name
      BACKEND_BUCKET_NAME = aws_s3_bucket.backend.bucket
      JOB_QUEUE_URL       = aws_sqs_queue.job_requests.id
    }
  }
}

# ================================================================================
# CloudWatch log group
# ================================================================================

resource "aws_cloudwatch_log_group" "lambda_logs" {

  name = "/aws/lambda/${aws_lambda_function.api.function_name}"

  retention_in_days = 7
}

# ================================================================================
# Worker Lambda function
# Processes SQS job requests
# ================================================================================

resource "aws_lambda_function" "worker" {

  function_name = "resume-worker-${random_id.bucket_suffix.hex}"

  filename         = data.archive_file.lambdas_zip.output_path
  source_code_hash = data.archive_file.lambdas_zip.output_base64sha256

  handler = "worker.lambda_handler"
  runtime = "python3.11"

  role = aws_iam_role.lambda_exec.arn

  timeout     = 300
  memory_size = 512

  environment {
    variables = {
      TABLE_NAME          = aws_dynamodb_table.app_table.name
      BACKEND_BUCKET_NAME = aws_s3_bucket.backend.bucket
      JOB_QUEUE_URL       = aws_sqs_queue.job_requests.id
      BEDROCK_MODEL_ID    = var.bedrock_model_id
    }
  }
}