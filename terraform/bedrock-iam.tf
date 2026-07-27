data "aws_caller_identity" "current" {}

# Trust policy: allow the agent's K8s ServiceAccount (via the cluster OIDC provider) to assume this role
data "aws_iam_policy_document" "agent_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(module.eks.oidc_provider, "https://", "")}:sub"
      values   = ["system:serviceaccount:default:remediation-agent"]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(module.eks.oidc_provider, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent_bedrock" {
  name               = "remediation-agent-bedrock"
  assume_role_policy = data.aws_iam_policy_document.agent_assume.json
  tags               = local.tags
}

# Permission: invoke Bedrock models (+ inference profiles)
resource "aws_iam_role_policy" "agent_bedrock" {
  name = "bedrock-invoke"
  role = aws_iam_role.agent_bedrock.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = "*"
    }]
  })
}

output "agent_bedrock_role_arn" {
  value = aws_iam_role.agent_bedrock.arn
}
