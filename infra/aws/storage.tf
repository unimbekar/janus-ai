resource "aws_ecr_repository" "service" {
  for_each             = toset(local.ecr_repos)
  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.environment != "prod"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_s3_bucket" "attachments" {
  bucket = "${local.name}-attachments-${local.account_id}"
}

resource "aws_s3_bucket_public_access_block" "attachments" {
  bucket                  = aws_s3_bucket.attachments.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_secretsmanager_secret" "app" {
  name = "${local.name}/app"
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    JANUS_DATABASE_URL = format(
      "postgresql+asyncpg://janus_app:%s@%s:5432/janus",
      urlencode(random_password.db.result),
      aws_rds_cluster.this.endpoint,
    )
    JANUS_MIGRATION_DATABASE_URL = format(
      "postgresql+asyncpg://%s:%s@%s:5432/janus",
      var.db_username,
      urlencode(random_password.db.result),
      aws_rds_cluster.this.endpoint,
    )
    JANUS_GATEWAY_SERVICE_TOKEN = random_password.gateway_token.result
    JANUS_REDIS_URL = format(
      "rediss://%s:6379/0",
      aws_elasticache_replication_group.this.primary_endpoint_address,
    )
    JANUS_APP_DB_PASSWORD = random_password.db.result
  })
}
