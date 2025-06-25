import boto3
import docker
import logging
from typing import Dict

class AWSService:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.docker_client = docker.from_env()

    def get_ecr_credentials(self, region: str) -> Dict[str, str]:
        """Retrieve ECR credentials using AWS SDK"""
        try:
            ecr_client = boto3.client('ecr', region_name=region)
            response = ecr_client.get_authorization_token()
            auth_data = response['authorizationData'][0]
            token = auth_data['authorizationToken']
            endpoint = auth_data['proxyEndpoint']
            return {
                'username': 'AWS',
                'password': token,
                'registry': endpoint
            }
        except Exception as e:
            self.logger.error(f"Failed to get ECR credentials: {str(e)}")
            raise

    def push_image_to_ecr(self, ecr_tag: str, region: str) -> Dict[str, any]:
        """Push a Docker image to ECR and return structured results"""
        logs = []
        try:
            logs.append(f"Starting ECR push for image: {ecr_tag} in region: {region}")

            # Get ECR credentials
            credentials = self.get_ecr_credentials(region)
            logs.append("Retrieved ECR credentials successfully")

            # Login to ECR
            self.docker_client.login(
                username=credentials['username'],
                password=credentials['password'],
                registry=credentials['registry']
            )
            # Mask credentials in logs
            login_msg = f"Successfully logged in to ECR registry: {credentials['registry']}"
            logs.append(login_msg)
            self.logger.info(login_msg)
            self.logger.debug(f""
                ECR Login Details:
                Username: {credentials['username']}
                Registry: {credentials['registry']}
                Password: [REDACTED]
            """)

            # Push the image and capture logs
            logs.append(f"Pushing image: {ecr_tag}")
            push_output = []
            for line in self.docker_client.images.push(ecr_tag, stream=True, decode=True):
                if 'status' in line:
                    status = f"Push status: {line['status']}"
                    if 'progress' in line:
                        status += f" - {line['progress']}"
                    logs.append(status)
                    push_output.append(line)
                elif 'error' in line:
                    error_msg = f"Push error: {line['error']}"
                    logs.append(error_msg)
                    self.logger.error(error_msg)
                    return {
                        'success': False,
                        'error': error_msg,
                        'logs': logs
                    }

            success_msg = f"Image pushed successfully to ECR: {ecr_tag}"
            logs.append(success_msg)
            self.logger.info(success_msg)
            return {
                'success': True,
                'ecr_tag': ecr_tag,
                'logs': logs
            }
        except Exception as e:
            error_msg = f"ECR push failed: {str(e)}"
            logs.append(error_msg)
            self.logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'logs': logs
            }
