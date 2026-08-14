# Phase 8 GPU fleet — off by default.
#
# Enabling this creates an EKS control plane only. Node groups, GPU operators,
# and vLLM charts are deliberately not auto-applied: they need capacity
# reservations and cost approval.

resource "aws_eks_cluster" "gpu" {
  count    = var.enable_gpu_eks ? 1 : 0
  name     = "${local.name}-gpu"
  role_arn = aws_iam_role.eks_cluster[0].arn
  version  = "1.30"

  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    endpoint_private_access = true
    endpoint_public_access  = false
    security_group_ids      = [aws_security_group.ecs.id]
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy,
  ]
}

resource "aws_iam_role" "eks_cluster" {
  count = var.enable_gpu_eks ? 1 : 0
  name  = "${local.name}-eks-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  count      = var.enable_gpu_eks ? 1 : 0
  role       = aws_iam_role.eks_cluster[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}
