import subprocess
import logging
import os
import shutil
import tempfile
from typing import Optional, List

# Assuming get_logger is correctly set up in logging_config
# from app.core.logging_config import get_logger
# logger = get_logger(__name__)
# For standalone testing or if the above is not available yet:
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


def _run_command(command: List[str], env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """Helper function to run a subprocess command."""
    try:
        current_env = os.environ.copy()
        if env:
            current_env.update(env)

        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=current_env
        )
        # Log stdout only if it's not excessively long or sensitive
        stdout_log = process.stdout.strip()
        if len(stdout_log) > 1000: # Avoid logging huge outputs like kubeconfig
            stdout_log = stdout_log[:500] + "... (truncated)"
        logger.debug(f"Command '{' '.join(command)}' stdout:\n{stdout_log}")

        if process.stderr.strip(): # Log stderr only if it's not empty
            logger.debug(f"Command '{' '.join(command)}' stderr:\n{process.stderr.strip()}")
        return process
    except FileNotFoundError:
        logger.error(f"Command not found: {command[0]}. Please ensure it is installed and in PATH.")
        return subprocess.CompletedProcess(command, -1, stdout="", stderr=f"Command not found: {command[0]}")
    except Exception as e:
        logger.error(f"Exception running command '{' '.join(command)}': {str(e)}", exc_info=True)
        return subprocess.CompletedProcess(command, -1, stdout="", stderr=str(e))


def detect_kind_cluster(cluster_name: str) -> bool:
    """
    Detects if a Kind cluster with the given name is currently running.
    """
    logger.info(f"Detecting Kind cluster: {cluster_name}")
    kind_path = shutil.which('kind')
    if not kind_path:
        logger.error("kind command not found for detect_kind_cluster. Please ensure it is installed and in PATH.")
        return False
    command = [kind_path, 'get', 'clusters']
    process = _run_command(command)

    if process.returncode == 0:
        running_clusters = process.stdout.strip().split('\n')
        logger.debug(f"Available Kind clusters: {running_clusters}")
        if cluster_name in running_clusters:
            logger.info(f"Kind cluster '{cluster_name}' found.")
            return True
        else:
            logger.info(f"Kind cluster '{cluster_name}' not found among {running_clusters}.")
            return False
    else:
        logger.error(f"Failed to get Kind clusters list. Error: {process.stderr.strip()}")
        return False


def apply_calico(cluster_name: str, calico_yaml_url: str) -> bool:
    """
    Applies Calico CNI to the specified Kind cluster using the provided manifest URL.
    Uses an explicit kubeconfig file obtained from 'kind export kubeconfig'.
    """
    logger.info(f"Applying Calico CNI from '{calico_yaml_url}' to Kind cluster '{cluster_name}'")

    kubectl_path = shutil.which('kubectl')
    if not kubectl_path:
        logger.error("kubectl command not found. Please ensure it is installed and in PATH.")
        return False
    logger.info(f"Using kubectl found at: {kubectl_path}")

    kind_path = shutil.which('kind')
    if not kind_path:
        logger.error("kind command not found. Please ensure it is installed and in PATH.")
        return False
    logger.info(f"Using kind found at: {kind_path}")

    kubeconfig_export_command = [kind_path, 'export', 'kubeconfig', '--name', cluster_name]
    logger.info(f"Attempting to export kubeconfig for cluster '{cluster_name}': {' '.join(kubeconfig_export_command)}")

    kubeconfig_process = _run_command(kubeconfig_export_command)

    if kubeconfig_process.returncode != 0:
        logger.error(f"Failed to export kubeconfig for cluster '{cluster_name}'. Error:\n{kubeconfig_process.stderr.strip()}")
        return False

    kubeconfig_content = kubeconfig_process.stdout

    temp_kubeconfig_path = None # Initialize to ensure it's defined in finally
    try:
        # Create a temporary file to store the kubeconfig
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.yaml') as tmpfile:
            tmpfile.write(kubeconfig_content)
            temp_kubeconfig_path = tmpfile.name
        logger.info(f"Kubeconfig for cluster '{cluster_name}' saved to temporary file: {temp_kubeconfig_path}")

        apply_command = [kubectl_path, 'apply', '-f', calico_yaml_url, '--kubeconfig', temp_kubeconfig_path]
        logger.info(f"Executing Calico apply command: {' '.join(apply_command)}")

        apply_process = _run_command(apply_command)

        if apply_process.returncode == 0:
            logger.info(f"Calico CNI applied successfully to cluster '{cluster_name}'. Output:\n{apply_process.stdout.strip()}")
            return True
        else:
            logger.error(f"Failed to apply Calico CNI to cluster '{cluster_name}'. Error:\n{apply_process.stderr.strip()}")
            return False
    except Exception as e:
        logger.error(f"An exception occurred during Calico application: {str(e)}", exc_info=True)
        return False
    finally:
        if temp_kubeconfig_path and os.path.exists(temp_kubeconfig_path):
            try:
                os.remove(temp_kubeconfig_path)
                logger.info(f"Temporary kubeconfig file '{temp_kubeconfig_path}' removed.")
            except OSError as e:
                logger.error(f"Error removing temporary kubeconfig file '{temp_kubeconfig_path}': {str(e)}", exc_info=True)


def create_kind_cluster(
    cluster_name: str,
    config_path: Optional[str] = None,
    calico_yaml_url: Optional[str] = None
) -> bool:
    """
    Creates a new Kind cluster with the given name, optionally using a config file
    and applying a Calico CNI manifest.
    """
    if detect_kind_cluster(cluster_name): # detect_kind_cluster now checks for kind path
        logger.info(f"Kind cluster '{cluster_name}' already exists.")
        if calico_yaml_url:
            logger.info(f"Attempting to apply Calico CNI from {calico_yaml_url} to existing cluster '{cluster_name}'.")
            if not apply_calico(cluster_name, calico_yaml_url): # apply_calico checks for kind & kubectl
                 logger.warning(f"Failed to apply Calico to existing cluster '{cluster_name}'. It might not be configured correctly.")
            else:
                logger.info(f"Calico CNI (re-)applied/verified for existing cluster '{cluster_name}'.")
        return True

    logger.info(f"Attempting to create Kind cluster: '{cluster_name}'")
    kind_path = shutil.which('kind')
    if not kind_path:
        logger.error("kind command not found. Please ensure it is installed and in PATH.")
        return False

    command = [kind_path, 'create', 'cluster', '--name', cluster_name]
    if config_path:
        logger.info(f"Using Kind configuration file: {config_path}")
        command.extend(['--config', config_path])

    logger.info(f"Executing Kind create command: {' '.join(command)}")
    process = _run_command(command)

    if process.returncode == 0:
        logger.info(f"Kind cluster '{cluster_name}' created successfully.")
        if calico_yaml_url:
            logger.info(f"Attempting to apply Calico CNI from {calico_yaml_url} to new cluster '{cluster_name}'.")
            if apply_calico(cluster_name, calico_yaml_url): # apply_calico checks for kind & kubectl
                logger.info(f"Calico CNI applied successfully to new cluster '{cluster_name}'.")
                return True
            else:
                logger.error(f"Failed to apply Calico CNI to new cluster '{cluster_name}'. Cluster is up, but CNI is missing.")
                logger.info(f"Attempting to delete cluster '{cluster_name}' due to Calico application failure.")
                delete_kind_cluster(cluster_name) # delete_kind_cluster checks for kind path
                return False
        else:
            logger.info(f"Kind cluster '{cluster_name}' created without Calico CNI (no URL provided).")
            return True
    else:
        logger.error(f"Failed to create Kind cluster '{cluster_name}'. Error:\n{process.stderr.strip()}")
        return False

def delete_kind_cluster(cluster_name: str) -> bool:
    """Deletes a Kind cluster with the given name."""
    logger.info(f"Attempting to delete Kind cluster: {cluster_name}")

    kind_path = shutil.which('kind') # Ensure kind is available for deletion logic too
    if not kind_path:
        logger.error("kind command not found for delete_kind_cluster. Please ensure it is installed and in PATH.")
        return False

    # detect_kind_cluster already checks for kind_path.
    # No need to call shutil.which('kind') again if detect_kind_cluster is called first.
    # However, if detect_kind_cluster was not called first, this check is useful.
    # For safety, let's ensure detect_kind_cluster is robust or repeat the check.
    # Current detect_kind_cluster has the check.

    if not detect_kind_cluster(cluster_name):
        logger.info(f"Kind cluster '{cluster_name}' does not exist. Skipping deletion.")
        return True

    command = [kind_path, 'delete', 'cluster', '--name', cluster_name]
    process = _run_command(command)
    if process.returncode == 0:
        logger.info(f"Kind cluster '{cluster_name}' deleted successfully.")
        return True
    else:
        logger.error(f"Failed to delete Kind cluster '{cluster_name}'. Error:\n{process.stderr.strip()}")
        return False

def load_image_into_kind(image_name_tag: str, cluster_name: str) -> bool:
    """
    Loads a Docker image from the local Docker daemon into the specified Kind cluster.

    Args:
        image_name_tag: The name and tag of the Docker image to load (e.g., 'myimage:latest').
        cluster_name: The name of the target Kind cluster.

    Returns:
        True if the image was loaded successfully, False otherwise.
    """
    logger.info(f"Attempting to load image '{image_name_tag}' into Kind cluster '{cluster_name}'.")

    kind_path = shutil.which('kind')
    if not kind_path:
        logger.error("kind command not found for load_image_into_kind. Please ensure it is installed and in PATH.")
        return False

    command = [kind_path, 'load', 'docker-image', image_name_tag, '--name', cluster_name]
    logger.info(f"Executing Kind load command: {' '.join(command)}")

    process = _run_command(command)

    if process.returncode == 0:
        logger.info(f"Image '{image_name_tag}' loaded successfully into cluster '{cluster_name}'.\n{process.stdout.strip()}")
        return True
    else:
        logger.error(f"Failed to load image '{image_name_tag}' into cluster '{cluster_name}'. Error:\n{process.stderr.strip()}")
        return False
