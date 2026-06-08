# ================================================================================
# DynamoDB table
# Stores metadata for resumes and jobs
# ================================================================================

resource "aws_dynamodb_table" "app_table" {
  name         = "resume-app-${random_id.bucket_suffix.hex}"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "pk"
  range_key = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  tags = {
    Name = "resume-app"
  }
}