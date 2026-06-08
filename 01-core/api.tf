# ================================================================================
# API Gateway HTTP API
# ================================================================================

resource "aws_apigatewayv2_api" "api" {
  name          = "resume-api-${random_id.bucket_suffix.hex}"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = [
      "https://${aws_s3_bucket.frontend.bucket}.s3.${data.aws_region.current.region}.amazonaws.com"
    ]

    allow_methods = [
      "GET",
      "POST",
      "PUT",
      "PATCH",
      "DELETE",
      "OPTIONS"
    ]

    allow_headers = [
      "*"
    ]

    max_age = 300
  }
}

# ================================================================================
# Cognito JWT authorizer
# ================================================================================

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id          = aws_apigatewayv2_api.api.id
  name            = "resume-cognito-jwt"
  authorizer_type = "JWT"

  identity_sources = [
    "$request.header.Authorization"
  ]

  jwt_configuration {
    audience = [
      aws_cognito_user_pool_client.resume_app.id
    ]

    issuer = join("", [
      "https://cognito-idp.",
      data.aws_region.current.region,
      ".amazonaws.com/",
      aws_cognito_user_pool.resume_app.id
    ])
  }
}

# ================================================================================
# Lambda integration
# ================================================================================

resource "aws_apigatewayv2_integration" "lambda" {
  api_id = aws_apigatewayv2_api.api.id

  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

# ================================================================================
# Routes
# ================================================================================

resource "aws_apigatewayv2_route" "get_folders" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /folders"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "create_folder" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "POST /folders"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "delete_folder" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "DELETE /folders/{folder_id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "move_job_to_folder" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "PATCH /jobs/{job_id}/folder"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "get_jobs" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /jobs"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "create_job" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "POST /jobs"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "get_job" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /jobs/{job_id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "update_job_notes" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "PATCH /jobs/{job_id}/notes"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "get_resumes" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /resumes"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "create_resume" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "POST /resumes"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "get_resume" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /resumes/{resume_id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "update_resume" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "PUT /resumes/{resume_id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "delete_resume" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "DELETE /resumes/{resume_id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "delete_job" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "DELETE /jobs/{job_id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# ================================================================================
# User registration and token usage routes
# ================================================================================

resource "aws_apigatewayv2_route" "register_user" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "POST /register"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "get_usage" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /usage"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# ================================================================================
# Attachment routes
# ================================================================================

resource "aws_apigatewayv2_route" "list_attachments" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /jobs/{job_id}/attachments"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "upload_attachment" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "POST /jobs/{job_id}/attachments"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "download_attachment" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "GET /jobs/{job_id}/attachments/{attachment_id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

resource "aws_apigatewayv2_route" "delete_attachment" {
  api_id             = aws_apigatewayv2_api.api.id
  route_key          = "DELETE /jobs/{job_id}/attachments/{attachment_id}"
  target             = "integrations/${aws_apigatewayv2_integration.lambda.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}

# ================================================================================
# Stage
# ================================================================================

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
}

# ================================================================================
# Allow API Gateway to invoke Lambda
# ================================================================================

resource "aws_lambda_permission" "api_gw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# ================================================================================
# API endpoint output
# ================================================================================

output "api_endpoint" {
  description = "Base URL for the API Gateway endpoint"
  value       = aws_apigatewayv2_api.api.api_endpoint
}
