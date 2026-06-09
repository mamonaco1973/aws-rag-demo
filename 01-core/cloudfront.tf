# ================================================================================
# ACM Certificate
# Must be in us-east-1 — CloudFront requires certificates in this region
# ================================================================================

resource "aws_acm_certificate" "askmike" {
  domain_name       = "askmike.mikes-cloud-solutions.com"
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# ================================================================================
# Route53 DNS validation records
# Zone ID is the mikes-cloud-solutions.com hosted zone
# ================================================================================

resource "aws_route53_record" "askmike_cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.askmike.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id         = "Z104804116GKVC6IA1EKC"
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

# ================================================================================
# Wait for ACM to validate before creating CloudFront distribution
# ================================================================================

resource "aws_acm_certificate_validation" "askmike" {
  certificate_arn         = aws_acm_certificate.askmike.arn
  validation_record_fqdns = [for r in aws_route53_record.askmike_cert_validation : r.fqdn]
}

# ================================================================================
# CloudFront distribution
# S3 website endpoint as HTTP origin — CloudFront provides TLS at the edge
# ================================================================================

resource "aws_cloudfront_distribution" "askmike" {
  enabled             = true
  default_root_object = "index.html"
  aliases             = ["askmike.mikes-cloud-solutions.com"]

  origin {
    domain_name = "${aws_s3_bucket.frontend.bucket}.s3-website-${var.region}.amazonaws.com"
    origin_id   = "s3-frontend"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 3600
    max_ttl     = 86400
  }

  # Redirect SPA 403/404s to index.html for client-side routing
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }

  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.askmike.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  depends_on = [aws_acm_certificate_validation.askmike]
}

# ================================================================================
# Route53 A alias record → CloudFront distribution
# ================================================================================

resource "aws_route53_record" "askmike" {
  zone_id = "Z104804116GKVC6IA1EKC"
  name    = "askmike.mikes-cloud-solutions.com"
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.askmike.domain_name
    zone_id                = aws_cloudfront_distribution.askmike.hosted_zone_id
    evaluate_target_health = false
  }
}

# ================================================================================
# Outputs
# ================================================================================

output "custom_domain_url" {
  value = "https://askmike.mikes-cloud-solutions.com"
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.askmike.id
}
