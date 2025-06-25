import logging
import re
from typing import Dict, List, Optional
import docker

class DockerService:
    """Service for Docker operations including Dockerfile analysis and image building"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def analyze_dockerfile(self, dockerfile_path: str) -> Dict[str, any]:
        """
        Analyze a Dockerfile to extract exposed ports, base image, working directory,
        environment variables, and commands.

        Args:
            dockerfile_path: Path to the Dockerfile

        Returns:
            Dictionary containing:
                - 'base_image': Base image name and tag (e.g., 'ubuntu:latest')
                - 'exposed_ports': List of port numbers exposed via EXPOSE instructions
                - 'workdir': Working directory path
                - 'env_vars': Dictionary of environment variables
                - 'cmd': The CMD instruction value
                - 'entrypoint': The ENTRYPOINT instruction value
        """
        self.logger.info(f"Analyzing Dockerfile at: {dockerfile_path}")
        result = {
            'base_image': None,
            'exposed_ports': [],
            'workdir': None,
            'env_vars': {},
            'cmd': None,
            'entrypoint': None
        }

        try:
            with open(dockerfile_path, 'r') as f:
                content = f.read()

                # Find base image
                from_match = re.search(r'FROM\s+(\S]+)', content, re.IGNORECASE)
                if from_match:
                    result['base_image'] = from_match.group(1).strip()

                # Find exposed ports
                expose_matches = re.findall(r'EXPOSE\s+(\d+(?:\s+\d+)*)', content, re.IGNORECASE)
                for match in expose_matches:
                    ports = [int(port.strip()) for port in match.split() if port.strip().isdigit()]
                    result['exposed_ports'].extend(ports)

                # Find working directory
                workdir_match = re.search(r'WORKDIR\s+(\S+)', content, re.IGNORECASE)
                if workdir_match:
                    result['workdir'] = workdir_match.group(1).strip()

                # Find environment variables
                env_matches = re.findall(r'ENV\s+(\w+)\s*=\s*(\S+)', content, re.IGNORECASE)
                for key, value in env_matches:
                    result['env_vars'][key] = value

                # Find CMD instruction
                cmd_match = re.search(r'CMD\s+\[(.*?)\]', content, re.IGNORECASE)
                if not cmd_match:
                    cmd_match = re.search(r'CMD\s+(.*)', content, re.IGNORECASE)
                if cmd_match:
                    result['cmd'] = cmd_match.group(1).strip()

                # Find ENTRYPOINT instruction
                entrypoint_match = re.search(r'ENTRYPOINT\s+\[(.*?)\]', content, re.IGNORECASE)
                if not entrypoint_match:
                    entrypoint_match = re.search(r'ENTRYPOINT\s+(.*)', content, re.IGNORECASE)
                if entrypoint_match:
                    result['entrypoint'] = entrypoint_match.group(1).strip()

                self.logger.info(f"Dockerfile analysis completed: {result}")

        except FileNotFoundError:
            self.logger.error(f"Dockerfile not found at path: {dockerfile_path}")
        except Exception as e:
            self.logger.error(f"Error analyzing Dockerfile: {str(e)}")

        return result

    def build_image(self, context_path: str, dockerfile_path: str, image_tag: str) -> Dict[str, any]:
        """
        Build a Docker image using docker-py and capture build logs

        Args:
            context_path: Path to the build context
            dockerfile_path: Path to the Dockerfile
            image_tag: Tag for the built image

        Returns:
            Dictionary containing:
                - 'image_id': Image ID if build is successful, None otherwise
                - 'logs': List of build log entries
        """
        from .security_utils import sanitize_kubernetes_input

        # Sanitize image tag
        sanitized_image_tag = sanitize_kubernetes_input(image_tag)
        if not sanitized_image_tag:
            return {
                'image_id': None,
                'logs': ["ERROR: Invalid characters detected in image tag"]
            }

        self.logger.info(f"Building Docker image with tag: {sanitized_image_tag}")
        logs = []
        try:
            client = docker.from_env()
            # Build the image and capture logs
            logs.append(f"Starting build for image: {sanitized_image_tag}")
            build_output = client.images.build(
                path=context_path,
                dockerfile=dockerfile_path,
                tag=sanitized_image_tag,
                rm=True,  # Remove intermediate containers after build
                forcerm=True  # Always remove intermediate containers
            )
            # Process build logs
            image = build_output[0]
            build_logs = build_output[1]
            for chunk in build_logs:
                if 'stream' in chunk:
                    log_line = chunk['stream'].strip()
                    if log_line:
                        logs.append(log_line)
                        self.logger.info(log_line)
                elif 'error' in chunk:
                    log_line = f"ERROR: {chunk['error']}"
                    logs.append(log_line)
                    self.logger.error(log_line)
                elif 'status' in chunk:
                    log_line = f"Status: {chunk['status']}"
                    logs.append(log_line)
                    self.logger.info(log_line)

            self.logger.info(f"Successfully built image: {image.id}")
            logs.append(f"Build successful. Image ID: {image.id}")
            return {
                'image_id': image.id,
                'logs': logs
            }
        except docker.errors.BuildError as e:
            error_msg = f"Build failed: {str(e)}"
            self.logger.error(error_msg)
            logs.append(error_msg)
            # Capture build logs from exception
            for line in e.build_log:
                if 'stream' in line:
                    log_line = line['stream'].strip()
                    if log_line:
                        logs.append(log_line)
                        self.logger.error(log_line)
                elif 'error' in line:
                    log_line = f"ERROR: {line['error']}"
                    logs.append(log_line)
                    self.logger.error(log_line)
            return {
                'image_id': None,
                'logs': logs
            }
        except docker.errors.APIError as e:
            error_msg = f"Docker API error: {str(e)}"
            self.logger.error(error_msg)
            logs.append(error_msg)
            return {
                'image_id': None,
                'logs': logs
            }

    def tag_image_for_ecr(self, image_id: str, account_id: str, region: str, repo_name: str, tag: str = "latest") -> str:
        """Tag a Docker image for ECR repository using account, region, and repo name"""
        try:
            # Form the ECR repository URL
            ecr_repo = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}"
            ecr_tag = f"{ecr_repo}:{tag}"
            self.logger.info(f"Tagging image {image_id} for ECR: {ecr_tag}")
            client = docker.from_env()
            image = client.images.get(image_id)
            image.tag(ecr_tag)
            self.logger.info(f"Successfully tagged image: {ecr_tag}")
            return ecr_tag
        except docker.errors.ImageNotFound:
            self.logger.error(f"Image not found: {image_id}")
            raise
        except Exception as e:
            self.logger.error(f"Error tagging image: {str(e)}")
            raise

    def build_and_tag_for_ecr(self, context_path: str, dockerfile_path: str, image_tag: str, account_id: str, region: str, repo_name: str) -> Dict[str, any]:
        """Build Docker image and tag it for ECR using account, region, and repo name"""
        build_result = self.build_image(context_path, dockerfile_path, image_tag)
        if not build_result.get('image_id'):
            return build_result

        try:
            ecr_tag = self.tag_image_for_ecr(build_result['image_id'], account_id, region, repo_name, image_tag)
            build_result['ecr_tag'] = ecr_tag
        except Exception as e:
            build_result['error'] = f"ECR tagging failed: {str(e)}"
            build_result['ecr_tag'] = None

        return build_result
    def tag_image_for_ecr(self, image_id: str, ecr_repo: str, tag: str = "latest") -> str:
        """Tag a Docker image for ECR repository"""
        self.logger.info(f"Tagging image {image_id} for ECR: {ecr_repo}:{tag}")
        try:
            client = docker.from_env()
            image = client.images.get(image_id)
            ecr_tag = f"{ecr_repo}:{tag}"
            image.tag(ecr_tag)
            self.logger.info(f"Successfully tagged image: {ecr_tag}")
            return ecr_tag
        except docker.errors.ImageNotFound:
            self.logger.error(f"Image not found: {image_id}")
            raise
        except Exception as e:
            self.logger.error(f"Error tagging image: {str(e)}")
            raise

    def build_and_tag_for_ecr(self, context_path: str, dockerfile_path: str, image_tag: str, ecr_repo: str) -> Dict[str, any]:
        """Build Docker image and tag it for ECR"""
        build_result = self.build_image(context_path, dockerfile_path, image_tag)
        if not build_result.get('image_id'):
            return build_result

        try:
            ecr_tag = self.tag_image_for_ecr(build_result['image_id'], ecr_repo, image_tag)
            build_result['ecr_tag'] = ecr_tag
        except Exception as e:
            build_result['error'] = f"ECR tagging failed: {str(e)}"
            build_result['ecr_tag'] = None

        return build_result
