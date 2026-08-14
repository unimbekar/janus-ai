variable "aws_region" {
  type        = string
  description = "Primary AWS region for the US launch market."
  default     = "us-east-1"
}

variable "aws_account_id" {
  type        = string
  description = "Twelve-digit AWS account ID. Used for IAM and ECR ARNs."
}

variable "environment" {
  type        = string
  description = "Environment name (staging or prod)."
  default     = "staging"

  validation {
    condition     = contains(["staging", "prod", "dev"], var.environment)
    error_message = "environment must be staging, prod, or dev."
  }
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for resource names."
  default     = "janus"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "availability_zones" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b"]
}

variable "domain_name" {
  type        = string
  description = "Optional public hostname for the ALB listener certificate."
  default     = ""
}

variable "acm_certificate_arn" {
  type        = string
  description = "ACM certificate ARN for HTTPS. Leave empty to serve HTTP only (not for production)."
  default     = ""
}

variable "image_tag" {
  type        = string
  description = "Container image tag pushed to ECR."
  default     = "latest"
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "enable_gpu_eks" {
  type        = bool
  description = "Phase 8: create an EKS GPU cluster. Off by default."
  default     = false
}

variable "db_username" {
  type    = string
  default = "janus"
}

variable "web_cpu" {
  type    = number
  default = 256
}

variable "web_memory" {
  type    = number
  default = 512
}

variable "api_cpu" {
  type    = number
  default = 512
}

variable "api_memory" {
  type    = number
  default = 1024
}

variable "gateway_cpu" {
  type    = number
  default = 512
}

variable "gateway_memory" {
  type    = number
  default = 1024
}
