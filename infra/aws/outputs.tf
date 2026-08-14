output "account_id" {
  value = local.account_id
}

output "region" {
  value = var.aws_region
}

output "alb_dns_name" {
  value       = aws_lb.this.dns_name
  description = "Point your DNS or Marketplace listing at this hostname (or CloudFront in front of it)."
}

output "ecr_repositories" {
  value = { for name, repo in aws_ecr_repository.service : name => repo.repository_url }
}

output "aurora_endpoint" {
  value     = aws_rds_cluster.this.endpoint
  sensitive = true
}

output "redis_endpoint" {
  value     = aws_elasticache_replication_group.this.primary_endpoint_address
  sensitive = true
}

output "secrets_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "attachments_bucket" {
  value = aws_s3_bucket.attachments.bucket
}

output "ecs_cluster" {
  value = aws_ecs_cluster.this.name
}

output "gpu_eks_endpoint" {
  value = try(aws_eks_cluster.gpu[0].endpoint, null)
}
