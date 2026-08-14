resource "aws_db_subnet_group" "this" {
  name       = "${local.name}-aurora"
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_rds_cluster_parameter_group" "this" {
  name   = "${local.name}-aurora16"
  family = "aurora-postgresql16"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }
}

resource "aws_rds_cluster" "this" {
  cluster_identifier              = "${local.name}-aurora"
  engine                          = "aurora-postgresql"
  engine_version                  = "16.4"
  database_name                   = "janus"
  master_username                 = var.db_username
  master_password                 = random_password.db.result
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.data.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.this.name
  storage_encrypted               = true
  backup_retention_period         = 7
  preferred_backup_window         = "07:00-09:00"
  deletion_protection             = var.environment == "prod"
  skip_final_snapshot             = var.environment != "prod"
  apply_immediately               = var.environment != "prod"
  enable_http_endpoint            = false

  serverlessv2_scaling_configuration {
    min_capacity = 0.5
    max_capacity = 4
  }
}

resource "aws_rds_cluster_instance" "this" {
  identifier         = "${local.name}-aurora-1"
  cluster_identifier = aws_rds_cluster.this.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.this.engine
  engine_version     = aws_rds_cluster.this.engine_version
}

resource "aws_elasticache_subnet_group" "this" {
  name       = "${local.name}-redis"
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id       = "${local.name}-redis"
  description                = "Janus rate limits and shared cache"
  engine                     = "redis"
  engine_version             = "7.1"
  node_type                  = "cache.t4g.micro"
  num_cache_clusters         = 2
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.this.name
  security_group_ids         = [aws_security_group.data.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  automatic_failover_enabled = true
}
