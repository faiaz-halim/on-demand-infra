import docker
from docker.errors import BuildError, APIError, DockerException
from pathlib import Path
from typing import Dict, Optional, Union, List, Tuple
from app.core.logging_config import get_logger
import boto3
import base64
import docker # Already partially imported, ensure full module access if needed

logger = get_logger(__name__)

def build_docker_image_locally(
    context_path: Union[str, Path],
    image_tag: str,
    dockerfile_name: Optional[str] = "Dockerfile" # Defaulting to "Dockerfile"
) -> Dict:
    """
    Builds a Docker image locally using the Docker SDK.

    Args:
        context_path: Path to the build context (directory containing the Dockerfile).
        image_tag: Tag to apply to the built image (e.g., 'myapp:latest').
        dockerfile_name: Name of the Dockerfile within the context_path.
                         If None, Docker daemon defaults (usually 'Dockerfile').

    Returns:
        A dictionary containing build status, image ID, tags, and logs.
    """
    logs_list: List[str] = []
    # Resolve context_path to an absolute path string
    context_path_str = str(Path(context_path).resolve())

    # dockerfile_name should be relative to the context_path
    # If dockerfile_name is an absolute path, make it relative or just use the name.
    # For simplicity, we assume dockerfile_name is just the filename within the context.
    relative_dockerfile_name = dockerfile_name if dockerfile_name else "Dockerfile"

    logger.info(f"Attempting to build Docker image with tag '{image_tag}' from context '{context_path_str}' using Dockerfile '{relative_dockerfile_name}'")

    try:
        client = docker.from_env()
        client.ping()
        logger.debug("Docker client initialized and daemon is responsive.")
    except DockerException as e:
        logger.error(f"Docker daemon not running or inaccessible: {str(e)}")
        return {"success": False, "error": f"Docker daemon not running or inaccessible: {str(e)}", "logs": []}
    except Exception as e:
        logger.error(f"Failed to initialize Docker client: {str(e)}")
        return {"success": False, "error": f"Failed to initialize Docker client: {str(e)}", "logs": []}

    try:
        image, logs_generator = client.images.build(
            path=context_path_str,
            tag=image_tag,
            dockerfile=relative_dockerfile_name, # This should be the name of the Dockerfile within the path
            rm=True,
            forcerm=True
        )

        for log_entry in logs_generator:
            if 'stream' in log_entry:
                log_line = log_entry['stream'].strip()
                logs_list.append(log_line)
                logger.debug(f"Build log: {log_line}")
            elif 'errorDetail' in log_entry:
                error_detail = log_entry['errorDetail']['message']
                logs_list.append(f"ERROR: {error_detail}")
                logger.error(f"Build error detail: {error_detail}")

        logger.info(f"Successfully built image '{image_tag}'. ID: {image.id}, Tags: {image.tags}")
        return {
            "success": True,
            "image_id": image.id,
            "image_tags": image.tags,
            "logs": "\n".join(logs_list)
        }
    except BuildError as e:
        logger.error(f"Docker image build failed for tag '{image_tag}': {str(e)}", exc_info=True)
        # Capture logs from BuildError if available
        if hasattr(e, 'build_log') and e.build_log:
            for log_entry in e.build_log:
                 if isinstance(log_entry, dict):
                    if 'stream' in log_entry:
                        logs_list.append(log_entry['stream'].strip())
                    elif 'error' in log_entry:
                        logs_list.append(f"ERROR: {log_entry['error']}")
        return {
            "success": False,
            "error": f"Build failed: {str(e)}",
            "logs": "\n".join(logs_list)
        }
    except APIError as e:
        logger.error(f"Docker API error during build for tag '{image_tag}': {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Docker API error: {str(e)}",
            "logs": "\n".join(logs_list)
        }
    except Exception as e:
        logger.error(f"An unexpected error occurred during image build for tag '{image_tag}': {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}",
            "logs": "\n".join(logs_list)
        }
# --- ECR Integration Functions ---

def get_ecr_login_details(
    aws_region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str
) -> Optional[Tuple[str, str, str]]: # Returns (username, password, proxy_endpoint/registry_url)
    logger.info(f"Attempting to get ECR login token for region '{aws_region}'.")
    try:
        ecr_client = boto3.client(
            'ecr',
            region_name=aws_region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key
        )
        response = ecr_client.get_authorization_token()
        auth_data = response.get('authorizationData')
        if not auth_data:
            logger.error("No authorizationData found in ECR get_authorization_token response.")
            return None

        token = auth_data[0]['authorizationToken'] # This is a base64 encoded string
        proxy_endpoint = auth_data[0]['proxyEndpoint'] # This is the registry URL

        # Decode the token
        decoded_token = base64.b64decode(token).decode('utf-8')
        username, password = decoded_token.split(':') # Format is "AWS:<password>" or just "<user>:<password>"

        logger.info(f"Successfully retrieved ECR login token. Username: {username}, Registry: {proxy_endpoint}")
        return username, password, proxy_endpoint
    except Exception as e:
        logger.error(f"Failed to get ECR login token: {str(e)}", exc_info=True)
        return None

def login_to_ecr(docker_client: docker.DockerClient, registry_url: str, username: str, password: str) -> bool:
    logger.info(f"Attempting to login to ECR registry: {registry_url}")
    try:
        # The registry_url from get_authorization_token often includes "https://" which login expects.
        login_result = docker_client.login(
            username=username,
            password=password,
            registry=registry_url
        )
        # login_result can be a dict e.g. {'Status': 'Login Succeeded', 'IdentityToken': '...'}
        # or just a string in some older versions or error cases.
        status = login_result.get('Status', str(login_result)) if isinstance(login_result, dict) else str(login_result)
        logger.info(f"ECR login successful. Status: {status}")
        return True
    except APIError as e:
        logger.error(f"ECR login failed for registry {registry_url}: {str(e)}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred during ECR login to {registry_url}: {str(e)}", exc_info=True)
        return False

def push_image_to_ecr(
    docker_client: docker.DockerClient,
    local_image_tag: str,
    ecr_repo_name: str,
    ecr_registry_url: str, # Full registry URL e.g. https://<account_id>.dkr.ecr.<region>.amazonaws.com
    image_version_tag: str = "latest"
) -> Optional[str]:

    # Construct the full ECR image URI. ECR registry URL might include "https://".
    # Docker tags usually don't have the scheme.
    clean_registry_url = ecr_registry_url.replace("https://", "").replace("http://", "")
    full_ecr_image_uri = f"{clean_registry_url}/{ecr_repo_name}:{image_version_tag}"

    logger.info(f"Attempting to tag local image '{local_image_tag}' as '{full_ecr_image_uri}'")

    try:
        image_to_push = docker_client.images.get(local_image_tag)
        if not image_to_push.tag(full_ecr_image_uri): # The tag method returns True on success
            logger.error(f"Failed to tag image {local_image_tag} as {full_ecr_image_uri}")
            return None
        logger.info(f"Successfully tagged image as {full_ecr_image_uri}")

        logger.info(f"Pushing image {full_ecr_image_uri} to ECR...")

        push_logs_combined = []
        error_in_stream = False
        # The push method returns a generator that streams log output.
        for line in docker_client.images.push(full_ecr_image_uri, stream=True, decode=True):
            if 'status' in line:
                log_line = line['status']
                if 'progressDetail' in line and line['progressDetail']:
                    progress = line['progressDetail']
                    # Ensure progress values are not None before trying to access them
                    current_progress = progress.get('current')
                    total_progress = progress.get('total')
                    if current_progress is not None and total_progress is not None:
                         log_line += f" (current: {current_progress}, total: {total_progress})"
                    elif current_progress is not None:
                         log_line += f" (current: {current_progress})"
                push_logs_combined.append(log_line)
                logger.debug(f"ECR push: {log_line}")
            if 'errorDetail' in line:
                error_detail = line['errorDetail']['message']
                push_logs_combined.append(f"ERROR: {error_detail}")
                logger.error(f"ECR push error detail: {error_detail}")
                error_in_stream = True # Mark that an error occurred
            elif 'error' in line: # Some errors might just have an 'error' key
                error_detail = line['error']
                push_logs_combined.append(f"ERROR: {error_detail}")
                logger.error(f"ECR push error: {error_detail}")
                error_in_stream = True

        if error_in_stream:
             logger.error(f"Failed to push image {full_ecr_image_uri} to ECR due to errors in stream. Logs: {' | '.join(push_logs_combined)}")
             return None

        logger.info(f"Successfully pushed image {full_ecr_image_uri} to ECR. Final logs: {' | '.join(push_logs_combined)}")
        return full_ecr_image_uri

    except APIError as e: # This might catch tagging errors or initial push errors
        logger.error(f"Docker API error during ECR push operations for {full_ecr_image_uri}: {str(e)}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred during ECR push for {full_ecr_image_uri}: {str(e)}", exc_info=True)
        return None
    except BuildError as e:
        logger.error(f"Docker image build failed for tag '{image_tag}': {str(e)}", exc_info=True)
        # Capture logs from BuildError if available
        if hasattr(e, 'build_log') and e.build_log:
            for log_entry in e.build_log:
                 if isinstance(log_entry, dict):
                    if 'stream' in log_entry:
                        logs_list.append(log_entry['stream'].strip())
                    elif 'error' in log_entry:
                        logs_list.append(f"ERROR: {log_entry['error']}")
        return {
            "success": False,
            "error": f"Build failed: {str(e)}",
            "logs": "\n".join(logs_list)
        }
    except APIError as e:
        logger.error(f"Docker API error during build for tag '{image_tag}': {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Docker API error: {str(e)}",
            "logs": "\n".join(logs_list)
        }
    except Exception as e:
        logger.error(f"An unexpected error occurred during image build for tag '{image_tag}': {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}",
            "logs": "\n".join(logs_list)
        }
