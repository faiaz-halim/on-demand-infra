import logging
import os
import docker
from typing import Dict, List

class DockerEngine:
    """Handles Docker image building and container operations"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

        # Initialize Docker client
        self.client = docker.from_env()
        self.logger.info("Docker client initialized")

    def build_image(self, dockerfile_path: str, image_name: str, tags: List[str] = ["latest"]) -> Dict:
        """Build Docker image from Dockerfile"""
        self.logger.info(f"Building image {image_name} from {dockerfile_path}")
        try:
            build_output = []
            image, logs = self.client.images.build(
                path=os.path.dirname(dockerfile_path),
                dockerfile=os.path.basename(dockerfile_path),
                tag=f"{image_name}:{tags[0]}",
                rm=True,
                forcerm=True
            )

            for log in logs:
                if 'stream' in log:
                    build_output.append(log['stream'].strip())

            return {
                "success": True,
                "image_id": image.id,
                "logs": build_output
            }
        except docker.errors.BuildError as e:
            self.logger.error(f"Build failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "logs": [log for log in e.build_log if 'stream' in log]
            }

    def push_image(self, image_name: str, registry_url: str, tags: List[str] = ["latest"], aws_credentials: Dict[str, str] = None) -> Dict:
        """Push Docker image to registry, with optional AWS ECR authentication"""
        self.logger.info(f"Pushing {image_name} to {registry_url}")
        try:
            image = self.client.images.get(f"{image_name}:{tags[0]}")
            image.tag(f"{registry_url}/{image_name}", tag=tags[0])

            # Handle ECR authentication if AWS credentials are provided
            if aws_credentials and "ecr" in registry_url:
                import boto3
                self.logger.info("Authenticating to ECR registry")

                # Create ECR client
                ecr_client = boto3.client(
                    'ecr',
                    region_name=aws_credentials.get('region', 'us-east-1'),
                    aws_access_key_id=aws_credentials['access_key_id'],
                    aws_secret_access_key=aws_credentials['secret_access_key']
                )

                # Get ECR authorization token
                auth_response = ecr_client.get_authorization_token()
                auth_data = auth_response['authorizationData'][0]
                token = auth_data['authorizationToken']
                endpoint = auth_data['proxyEndpoint']

                # Login to ECR
                self.client.login(
                    username="AWS",
                    password=token,
                    registry=endpoint
                )
                self.logger.info(f"Successfully authenticated to ECR: {endpoint}")

            push_log = self.client.images.push(
                f"{registry_url}/{image_name}",
                tag=tags[0],
                stream=True,
                decode=True
            )

            logs = []
            for line in push_log:
                logs.append(line)
                if 'error' in line:
                    self.logger.error(f"Push error: {line['error']}")
                    return {
                        "success": False,
                        "error": line['error'],
                        "logs": logs
                    }

            return {
                "success": True,
                "logs": logs
            }
        except docker.errors.APIError as e:
            self.logger.error(f"Push failed: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
        except Exception as e:
            self.logger.error(f"Unexpected error during push: {str(e)}")
            return {
                "success": False,
                "error": str(e)
            }
