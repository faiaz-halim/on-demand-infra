import json
import logging
from typing import Dict, Any

class TerraformModuleEngine:
    """Generates Terraform modules for various infrastructure components"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def generate_eks_cluster(self, cluster_name: str, region: str, node_type: str = "t3.medium", min_nodes: int = 1, max_nodes: int = 3, iam_role_arn: str = None) -> str:
        """Generate Terraform HCL for an EKS cluster"""
        from .security_utils import sanitize_terraform_input

        # Sanitize all inputs
        cluster_name = sanitize_terraform_input(cluster_name)
        region = sanitize_terraform_input(region)
        node_type = sanitize_terraform_input(node_type)
        if iam_role_arn:
            iam_role_arn = sanitize_terraform_input(iam_role_arn)

        # Validate required inputs
        if not cluster_name or not region or not node_type:
            self.logger.error("Invalid input detected in EKS cluster parameters")
            return "# ERROR: Invalid input detected in EKS cluster parameters"

        self.logger.info(f"Generating EKS cluster: {cluster_name} in {region}")

        # Base node group configuration
        node_group_config = f"""
      min_size     = {min_nodes}
      max_size     = {max_nodes}
      desired_size = {min_nodes}
      instance_types = ["{node_type}"]"""

        # Add IAM role if provided
        if iam_role_arn:
            node_group_config += f"\n      iam_role_arn  = \"{iam_role_arn}\""

        eks_module = f"""
module "eks_cluster" {{
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 19.0"

  cluster_name    = "{cluster_name}"
  cluster_version = "1.28"

  vpc_id     = "${{module.vpc.vpc_id}}"
  subnet_ids = "${{module.vpc.private_subnets}}"

  eks_managed_node_groups = {{
    default = {{{node_group_config}
    }}
  }}
}}
"""
        return eks_module

    def generate_ecr_repository(self, repo_name: str) -> str:
        """Generate Terraform HCL for an ECR repository"""
        from .security_utils import sanitize_terraform_input
        repo_name = sanitize_terraform_input(repo_name)

        self.logger.info(f"Generating ECR repository: {repo_name}")

        ecr_module = f"""
resource "aws_ecr_repository" "{repo_name.replace('-', '_')}" {{
  name = "{repo_name}"

  image_scanning_configuration {{
    scan_on_push = true
  }}
}}
"""
        return ecr_module

    def generate_eks_networking(self, cluster_name: str, domain: str, enable_acm: bool = True) -> str:
        """Generate Terraform HCL for EKS networking components (NLB, Route53, ACM)"""
        from .security_utils import sanitize_terraform_input
        cluster_name = sanitize_terraform_input(cluster_name)
        domain = sanitize_terraform_input(domain)

        self.logger.info(f"Generating EKS networking components for cluster: {cluster_name}")

        networking_module = f"""
# Network Load Balancer
resource "aws_lb" "nlb" {{
  name               = "${{var.cluster_name}}-nlb"
  internal           = false
  load_balancer_type = "network"
  subnets            = "${{module.vpc.public_subnets}}"

  enable_deletion_protection = false
}}

# ACM Certificate (if enabled)
{"resource \"aws_acm_certificate\" \"cert\" {\n  domain_name       = \"${domain}\"\n  validation_method = \"DNS\"\n}\n" if enable_acm else ""}

# Route53 Record
resource "aws_route53_record" "nlb_record" {{
  zone_id = var.route53_zone_id
  name    = "${{var.cluster_name}}.${{var.domain}}"
  type    = "A"

  alias {{
    name                   = aws_lb.nlb.dns_name
    zone_id                = aws_lb.nlb.zone_id
    evaluate_target_health = true
  }}
}}
"""
        return networking_module

    def generate_eks_iam_role(self, role_name: str) -> str:
        ... [existing code] ...
