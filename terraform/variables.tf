variable "region" {
  description = "AWS region (must match where the GPU quota was granted)"
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "llm-platform"
}

variable "cluster_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.31"
}

variable "cpu_instance_type" {
  description = "Instance type for the CPU node group"
  type        = string
  default     = "t3.medium"
}

variable "gpu_instance_type" {
  description = "Instance type for the GPU node group (NVIDIA)"
  type        = string
  default     = "g4dn.xlarge"
}
