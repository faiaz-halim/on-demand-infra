import logging
from .docker_engine import DockerEngine
from .kubernetes_manifest_engine import KubernetesManifestEngine
from .terraform_engine import TerraformEngine
from .aws_service import AWSService

class DeploymentOrchestrator:
    """Orchestrates deployment artifacts generation using various engines"""

    def __init__(self):
        self.docker_engine = DockerEngine()
        self.k8s_engine = KubernetesManifestEngine()
        self.tf_engine = TerraformEngine()
        self.aws_service = AWSService()  # Add AWS service integration

    def generate_local_deployment(self, app_name: str, image: str) -> dict:
        """Generate artifacts for local deployment (Docker)"""
        return {
            "dockerfile": self.docker_engine.generate_dockerfile(),
            "kubernetes": self.k8s_engine.generate_deployment(app_name, image),
            "service": self.k8s_engine.generate_service(app_name)
        }

    def generate_cloud_deployment(self, app_name: str, image: str, cluster_name: str) -> dict:
        """Generate artifacts for cloud deployment (EKS)"""
        return {
            "dockerfile": self.docker_engine.generate_dockerfile(),
            "kubernetes": self.k8s_engine.generate_deployment(app_name, image),
            "service": self.k8s_engine.generate_service(app_name),
            "terraform": self.tf_engine.generate_eks_config(cluster_name)
        }

class CloudHostedDeploymentHandler:
    """Handles cloud-hosted (EKS) deployment orchestration"""

    def __init__(self, orchestrator: DeploymentOrchestrator):
        self.orchestrator = orchestrator
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def setup_infrastructure(self, cluster_name: str) -> str:
        """Set up EKS cluster using Terraform"""
        try:
            self.logger.info(f"Generating Terraform configuration for EKS cluster: {cluster_name}")
            tf_config = self.orchestrator.tf_engine.generate_eks_config(cluster_name)

            self.logger.info(f"Applying Terraform configuration for EKS cluster: {cluster_name}")
            # Assuming TerraformEngine has an apply_config method
            apply_result = self.orchestrator.tf_engine.apply_config(tf_config)

            if apply_result.get('success'):
                self.logger.info(f"EKS cluster {cluster_name} created successfully")
                return f"EKS cluster {cluster_name} created"
            else:
                error_msg = f"Failed to create EKS cluster {cluster_name}: {apply_result.get('error')}"
                self.logger.error(error_msg)
                raise Exception(error_msg)
        except Exception as e:
            self.logger.error(f"Error setting up infrastructure: {str(e)}")
            raise

    def handle_image(self, app_name: str, image: str) -> str:
        """Build and push Docker image to ECR"""
        try:
            self.logger.info(f"Creating ECR repository for {app_name}")
            repo_uri = self.orchestrator.aws_service.create_ecr_repository(app_name)
            self.logger.info(f"ECR repository created: {repo_uri}")

            self.logger.info(f"Building Docker image: {image}")
            build_result = self.orchestrator.docker_engine.build_image(image)
            if not build_result.get('success'):
                error_msg = f"Failed to build image {image}: {build_result.get('error')}"
                self.logger.error(error_msg)
                raise Exception(error_msg)

            self.logger.info(f"Pushing image {image} to {repo_uri}")
            push_result = self.orchestrator.docker_engine.push_image(image, repo_uri)
            if not push_result.get('success'):
                error_msg = f"Failed to push image {image}: {push_result.get('error')}"
                self.logger.error(error_msg)
                raise Exception(error_msg)

            return f"{image} pushed to {repo_uri}"
        except Exception as e:
            self.logger.error(f"Error handling image: {str(e)}")
            raise

    def deploy_application(self, app_name: str, image: str) -> str:
        """Deploy application to EKS cluster"""
        try:
            self.logger.info(f"Generating Kubernetes manifests for {app_name}")
            deployment = self.orchestrator.k8s_engine.generate_deployment(app_name, image)
            service = self.orchestrator.k8s_engine.generate_service(app_name)
            ingress = self.orchestrator.k8s_engine.generate_ingress(app_name)

            self.logger.info("Applying Kubernetes manifests")
            # Assuming KubernetesManifestEngine has apply_manifest method
            self.orchestrator.k8s_engine.apply_manifest(deployment)
            self.orchestrator.k8s_engine.apply_manifest(service)
            self.orchestrator.k8s_engine.apply_manifest(ingress)

            return f"{app_name} deployed to EKS"
        except Exception as e:
            self.logger.error(f"Error deploying application: {str(e)}")
            raise

    def execute(self, app_name: str, image: str, cluster_name: str) -> dict:
        """Orchestrate full cloud-hosted deployment"""
        try:
            self.logger.info("Starting cloud-hosted deployment orchestration")

            # Emit initial status
            yield StreamingMessage(
                status="running",
                current_step="InfrastructureSetup",
                message="Starting infrastructure setup"
            )

            infra_result = self.setup_infrastructure(cluster_name)

            # Emit status update
            yield StreamingMessage(
                status="running",
                current_step="ImageHandling",
                message="Starting image build and push"
            )

            image_result = self.handle_image(app_name, image)

            # Emit status update
            yield StreamingMessage(
                status="running",
                current_step="ApplicationDeployment",
                message="Deploying application to cluster"
            )

            deploy_result = self.deploy_application(app_name, f"{image}:latest")

            # Emit success status
            yield StreamingMessage(
                status="completed",
                current_step="DeploymentComplete",
                message="Cloud-hosted deployment completed successfully"
            )

            return {
                "infrastructure": infra_result,
                "container_image": image_result,
                "deployment": deploy_result
            }
        except Exception as e:
            # Handle specific exceptions
            if isinstance(e, InfrastructureProvisioningError):
                error_type = "InfrastructureProvisioningError"
            elif isinstance(e, ApplicationBuildError):
                error_type = "ApplicationBuildError"
            else:
                error_type = "DeploymentError"

            # Emit error status
            yield StreamingMessage(
                status="error",
                current_step="DeploymentFailed",
                message=f"Deployment failed: {str(e)}",
                error_type=error_type,
                error_details={"exception": str(e)}
            )

            self.logger.error(f"Deployment orchestration failed: {str(e)}")
            return {
                "error": f"Deployment failed: {str(e)}"
            }
