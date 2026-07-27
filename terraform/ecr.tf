resource "aws_ecr_repository" "agent" {
  name                 = "remediation-agent"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  tags                 = local.tags
}

output "ecr_repo_url" {
  value = aws_ecr_repository.agent.repository_url
}
