import docker
from docker.errors import BuildError, APIError, DockerException
from pathlib import Path
from typing import Dict, Optional, Union, List
from app.core.logging_config import get_logger

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
