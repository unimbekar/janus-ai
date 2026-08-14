terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Uncomment after creating the bootstrap bucket/table (see docs/aws-deploy.md).
  # backend "s3" {
  #   bucket         = "janus-tfstate-<account-id>"
  #   key            = "staging/platform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "janus-tf-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "janus"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
