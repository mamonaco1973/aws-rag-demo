# ================================================================================
# Cognito locals
# ================================================================================

locals {
  spa_origin = "https://askmike.mikes-cloud-solutions.com"
}

# ================================================================================
# Cognito User Pool
# ================================================================================

resource "aws_cognito_user_pool" "rag_app" {
  name = "rag-app-user-pool-${random_id.bucket_suffix.hex}"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}

# ================================================================================
# Cognito Hosted UI domain
# ================================================================================

resource "aws_cognito_user_pool_domain" "rag_app" {
  domain       = "rag-app-auth-${random_id.bucket_suffix.hex}"
  user_pool_id = aws_cognito_user_pool.rag_app.id
}

# ================================================================================
# Cognito User Pool Client — SPA PKCE client
# ================================================================================

resource "aws_cognito_user_pool_client" "rag_app" {
  name         = "rag-app-spa-client-${random_id.bucket_suffix.hex}"
  user_pool_id = aws_cognito_user_pool.rag_app.id

  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH"
  ]

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  supported_identity_providers = ["COGNITO"]

  callback_urls = ["${local.spa_origin}/callback.html"]
  logout_urls   = ["${local.spa_origin}/index.html"]
}

# ================================================================================
# Outputs consumed by apply.sh to configure the frontend
# ================================================================================

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.rag_app.id
}

output "cognito_user_pool_client_id" {
  value = aws_cognito_user_pool_client.rag_app.id
}

output "cognito_domain" {
  value = aws_cognito_user_pool_domain.rag_app.domain
}

output "cognito_hosted_ui_base" {
  value = "https://${aws_cognito_user_pool_domain.rag_app.domain}.auth.${data.aws_region.current.region}.amazoncognito.com"
}
