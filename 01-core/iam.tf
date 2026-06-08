# ================================================================================
# Lambda execution role
# ================================================================================

resource "aws_iam_role" "lambda_exec" {
  name = "resume-app-lambda-${random_id.bucket_suffix.hex}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })
}

# ================================================================================
# CloudWatch logging policy
# ================================================================================

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# ================================================================================
# DynamoDB access policy
# ================================================================================

resource "aws_iam_policy" "lambda_dynamodb" {
  name = "resume-app-dynamodb-${random_id.bucket_suffix.hex}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          # Scan used to count registered users for the USER_CAP check
          "dynamodb:Scan"
        ]
        Resource = aws_dynamodb_table.app_table.arn
      }
    ]
  })
}

# ================================================================================
# Attach DynamoDB policy to Lambda role
# Grants Lambda functions read/write access to the application table
# ================================================================================

resource "aws_iam_role_policy_attachment" "lambda_dynamodb_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_dynamodb.arn
}

# ================================================================================
# S3 access policy
# ================================================================================

resource "aws_iam_policy" "lambda_s3" {
  name = "resume-app-s3-${random_id.bucket_suffix.hex}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BackendBucketList"
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.backend.arn
      },
      {
        Sid    = "BackendBucketObjects"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject"
        ]
        Resource = "${aws_s3_bucket.backend.arn}/users/*"
      }
    ]
  })
}

# ================================================================================
# Attach S3 policy to Lambda role
# Grants Lambda functions access to user data objects in the backend bucket
# ================================================================================

resource "aws_iam_role_policy_attachment" "lambda_s3_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_s3.arn
}

# ================================================================================
# SQS access policy
# Allows Lambda functions to send and process job scoring messages
# ================================================================================

resource "aws_iam_policy" "lambda_sqs" {
  name = "resume-app-sqs-${random_id.bucket_suffix.hex}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "JobQueueAccess"
        Effect = "Allow"
        Action = [
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
          "sqs:SendMessage",
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = [
          aws_sqs_queue.job_requests.arn,
          aws_sqs_queue.job_requests_dlq.arn
        ]
      }
    ]
  })
}

# ================================================================================
# Attach SQS policy to Lambda role
# Grants Lambda functions the ability to send and process job queue messages
# ================================================================================

resource "aws_iam_role_policy_attachment" "lambda_sqs_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_sqs.arn
}

# ================================================================================
# Bedrock access policy
# ================================================================================

resource "aws_iam_policy" "lambda_bedrock" {
  name = "resume-app-bedrock-${random_id.bucket_suffix.hex}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel"
        ]
        Resource = "*"
      }
    ]
  })
}

# ================================================================================
# Attach Bedrock policy to Lambda role
# Grants the worker Lambda permission to invoke AI models for scoring
# ================================================================================

resource "aws_iam_role_policy_attachment" "lambda_bedrock_attach" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = aws_iam_policy.lambda_bedrock.arn
}