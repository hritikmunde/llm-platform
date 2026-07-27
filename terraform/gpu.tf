# GPU node group for vLLM serving.
# Added as a separate managed node group so it can scale to 0 / be removed
# without touching the CPU nodes.
locals {
  gpu_node_group = {
    gpu = {
      instance_types = [var.gpu_instance_type]   # g4dn.xlarge (1x NVIDIA T4)
      min_size       = 1
      max_size       = 1
      desired_size   = 1

      # AL2_x86_64_GPU AMI ships with NVIDIA drivers preinstalled
      ami_type = "AL2_x86_64_GPU"
      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size = 100
            volume_type = "gp3"
          }
        }
      }

      # taint so ONLY GPU workloads (vLLM) land here, not random pods
      taints = {
        gpu = {
          key    = "nvidia.com/gpu"
          value  = "true"
          effect = "NO_SCHEDULE"
        }
      }

      labels = {
        "workload" = "gpu"
      }
    }
  }
}