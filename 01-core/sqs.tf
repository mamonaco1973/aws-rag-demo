# =================================================================================
# SQS dead-letter queue
# Stores messages that fail processing too many times
# =================================================================================

resource "aws_sqs_queue" "job_requests_dlq" {
  name = "job-requests-dlq"

  message_retention_seconds = 1209600
  receive_wait_time_seconds = 20

  tags = {
    Name = "job-requests-dlq"
  }
}

# =================================================================================
# SQS main queue
# Receives asynchronous job scoring requests
# =================================================================================

resource "aws_sqs_queue" "job_requests" {
  name = "job-requests"

  visibility_timeout_seconds = 1800
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.job_requests_dlq.arn
    maxReceiveCount     = 1
  })

  tags = {
    Name = "job-requests"
  }
}

# =================================================================================
# DLQ allow policy
# Allows the main queue to move failed messages into the DLQ
# =================================================================================

resource "aws_sqs_queue_redrive_allow_policy" "job_requests_dlq" {
  queue_url = aws_sqs_queue.job_requests_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.job_requests.arn]
  })
}

# ================================================================================
# SQS -> Worker Lambda event source mapping
# Connects the job request queue to worker.py
# ================================================================================

resource "aws_lambda_event_source_mapping" "worker_sqs" {
  event_source_arn = aws_sqs_queue.job_requests.arn
  function_name    = aws_lambda_function.worker.arn

  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
  enabled                            = true
}
