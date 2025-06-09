import subprocess
import logging
import os
import shutil
import tempfile
import pathlib
from typing import Optional, List, Dict

# Configure basic logging if this module is run standalone or if app logger isn't set up
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def _run_kubectl_command(command: List[str], kubeconfig_path: str) -> subprocess.CompletedProcess:
    """
    Helper function to run a kubectl command with a specific kubeconfig.
    """
    if not kubeconfig_path or not os.path.exists(kubeconfig_path):
        logger.error(f"Kubeconfig path '{kubeconfig_path}' is invalid or does not exist.")
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="Invalid kubeconfig path.")

    kubectl_path = shutil.which('kubectl')
    if not kubectl_path:
        logger.error("kubectl command not found. Please ensure it is installed and in PATH.")
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="kubectl not found.")

    base_env = os.environ.copy()
    base_env["KUBECONFIG"] = kubeconfig_path # Set KUBECONFIG for the command

    try:
        logger.debug(f"Running kubectl command: {' '.join([kubectl_path] + command)} using kubeconfig {kubeconfig_path}")
        process = subprocess.run(
            [kubectl_path] + command, # Prepend kubectl path to the command list
            capture_output=True,
            text=True,
            check=False, # Handle non-zero exit codes manually
            env=base_env
        )

        stdout_log = process.stdout.strip()
        stderr_log = process.stderr.strip()
        # Avoid overly verbose logging for successful common commands unless debug level is higher
        if process.returncode == 0:
            logger.debug(f"Kubectl command successful. stdout:\n{stdout_log}")
        else:
            logger.info(f"Kubectl command failed. stdout:\n{stdout_log}") # Log stdout on failure too
        if stderr_log:
            logger.info(f"Kubectl command stderr:\n{stderr_log}") # Log stderr always if present (often used for warnings too)
        return process
    except FileNotFoundError:
        logger.error(f"kubectl command not found at '{kubectl_path}'.")
        return subprocess.CompletedProcess(command, -1, stdout="", stderr=f"kubectl not found at '{kubectl_path}'.")
    except Exception as e:
        logger.error(f"Exception running kubectl command '{' '.join(command)}': {str(e)}", exc_info=True)
        return subprocess.CompletedProcess(command, -1, stdout="", stderr=str(e))


def _get_kind_kubeconfig(cluster_name: str) -> Optional[str]:
    """
    Exports the kubeconfig for a given Kind cluster and saves it to a temporary file.
    Returns the path to the temporary kubeconfig file, or None on failure.
    The caller is responsible for deleting the temporary file.
    """
    kind_path = shutil.which('kind')
    if not kind_path:
        logger.error("kind command not found. Please ensure it is installed and in PATH.")
        return None

    export_command = [kind_path, 'export', 'kubeconfig', '--name', cluster_name]
    logger.info(f"Attempting to export kubeconfig for Kind cluster '{cluster_name}': {' '.join(export_command)}")

    # Use a generic _run_command similar to kind_service if available, or direct subprocess
    # For now, direct subprocess as _run_command is not imported here
    process = subprocess.run(export_command, capture_output=True, text=True, check=False)

    if process.returncode != 0:
        logger.error(f"Failed to export kubeconfig for cluster '{cluster_name}'. Error:\n{process.stderr.strip()}")
        return None

    kubeconfig_content = process.stdout
    if not kubeconfig_content.strip():
        logger.error(f"Exported kubeconfig for cluster '{cluster_name}' is empty.")
        return None

    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.yaml', prefix=f"{cluster_name}-kubeconfig-") as tmpfile:
            tmpfile.write(kubeconfig_content)
            temp_kubeconfig_path = tmpfile.name
        logger.info(f"Kubeconfig for cluster '{cluster_name}' saved to temporary file: {temp_kubeconfig_path}")
        return temp_kubeconfig_path
    except Exception as e:
        logger.error(f"Failed to save kubeconfig to temporary file: {str(e)}", exc_info=True)
        return None


def create_namespace_if_not_exists(namespace: str, cluster_name: str) -> bool:
    """
    Creates a Kubernetes namespace if it doesn't already exist in the specified Kind cluster.
    """
    logger.info(f"Ensuring namespace '{namespace}' exists in cluster '{cluster_name}'.")
    kubeconfig_path = _get_kind_kubeconfig(cluster_name)
    if not kubeconfig_path:
        return False

    success_flag = False
    try:
        check_command = ['get', 'namespace', namespace, '-o', 'name']
        logger.debug(f"Checking if namespace '{namespace}' exists...")
        process_get = _run_kubectl_command(check_command, kubeconfig_path)

        if process_get.returncode == 0:
            logger.info(f"Namespace '{namespace}' already exists in cluster '{cluster_name}'.")
            success_flag = True
        # Check specific kubectl error for not found, as stderr might contain other warnings
        elif process_get.returncode != 0 and ("NotFound" in process_get.stderr or f"\"{namespace}\" not found" in process_get.stderr):
            logger.info(f"Namespace '{namespace}' not found. Attempting to create.")
            create_command = ['create', 'namespace', namespace]
            process_create = _run_kubectl_command(create_command, kubeconfig_path)
            if process_create.returncode == 0:
                logger.info(f"Namespace '{namespace}' created successfully in cluster '{cluster_name}'.")
                success_flag = True
            else:
                logger.error(f"Failed to create namespace '{namespace}'. Error:\n{process_create.stderr.strip()}")
                success_flag = False
        else:
            logger.error(f"Error checking for namespace '{namespace}'. Exit code {process_get.returncode}. Stderr: {process_get.stderr.strip()}")
            success_flag = False

    finally:
        if kubeconfig_path and os.path.exists(kubeconfig_path):
            try:
                os.remove(kubeconfig_path)
                logger.debug(f"Temporary kubeconfig file '{kubeconfig_path}' removed.")
            except OSError as e:
                logger.error(f"Error removing temporary kubeconfig file '{kubeconfig_path}': {e}", exc_info=True)
    return success_flag


def apply_manifests(manifest_dir_or_file: str, cluster_name: str, namespace: str = "default") -> bool:
    """
    Applies Kubernetes manifests from a directory or a single file to the specified Kind cluster.
    Ensures the namespace exists before applying.
    """
    logger.info(f"Applying manifests from '{manifest_dir_or_file}' to namespace '{namespace}' in cluster '{cluster_name}'.")

    # Ensure namespace exists first. This requires its own kubeconfig handling.
    # create_namespace_if_not_exists handles its own kubeconfig acquisition and cleanup.
    if not create_namespace_if_not_exists(namespace, cluster_name):
        logger.error(f"Failed to ensure namespace '{namespace}' exists or create it. Cannot apply manifests.")
        return False
    logger.info(f"Namespace '{namespace}' confirmed to exist for applying manifests.")

    kubeconfig_path = _get_kind_kubeconfig(cluster_name)
    if not kubeconfig_path:
        return False # Error already logged by _get_kind_kubeconfig

    success_flag = False
    try:
        path_obj = pathlib.Path(manifest_dir_or_file)
        if not path_obj.exists():
            logger.error(f"Manifest path '{manifest_dir_or_file}' does not exist.")
            return False # No need to set success_flag, already False

        apply_command = ['apply', '-f', str(path_obj), '--namespace', namespace]

        process = _run_kubectl_command(apply_command, kubeconfig_path)
        if process.returncode == 0:
            logger.info(f"Manifests from '{manifest_dir_or_file}' applied successfully to namespace '{namespace}'.\n{process.stdout.strip()}")
            success_flag = True
        else:
            logger.error(f"Failed to apply manifests from '{manifest_dir_or_file}'. Error:\n{process.stderr.strip()}")
            success_flag = False
    finally:
        if kubeconfig_path and os.path.exists(kubeconfig_path):
            try:
                os.remove(kubeconfig_path)
                logger.debug(f"Temporary kubeconfig file '{kubeconfig_path}' removed for apply_manifests.")
            except OSError as e:
                logger.error(f"Error removing temporary kubeconfig file '{kubeconfig_path}' for apply_manifests: {e}", exc_info=True)
    return success_flag


def delete_namespace_k8s(namespace: str, cluster_name: str) -> bool:
    """
    Deletes a Kubernetes namespace from the specified Kind cluster.
    """
    logger.info(f"Attempting to delete namespace '{namespace}' from cluster '{cluster_name}'.")
    kubeconfig_path = _get_kind_kubeconfig(cluster_name)
    if not kubeconfig_path:
        return False

    success_flag = False
    try:
        # --ignore-not-found=true makes the command idempotent from a CLI perspective
        delete_command = ['delete', 'namespace', namespace, '--ignore-not-found=true']
        process = _run_kubectl_command(delete_command, kubeconfig_path)

        # For `delete --ignore-not-found=true`, return code is 0 even if not found.
        # We might want to check stdout to see if it was actually deleted or if it didn't exist.
        # Example output if not found: "namespace "my-ns" not found" (still retcode 0)
        # Example output if found: "namespace "my-ns" deleted"
        if process.returncode == 0:
            if "not found" in process.stdout.lower(): # Check if kubectl indicated it was not found
                 logger.info(f"Namespace '{namespace}' was not found in cluster '{cluster_name}' (as reported by kubectl). Considered successful deletion/state.")
            else:
                logger.info(f"Namespace '{namespace}' deletion command executed successfully for cluster '{cluster_name}'.\n{process.stdout.strip()}")
            success_flag = True
        else:
            # This path might not be hit often with --ignore-not-found unless kubectl itself fails
            logger.error(f"Failed to execute delete namespace command for '{namespace}'. Error:\n{process.stderr.strip()}")
            success_flag = False
    finally:
        if kubeconfig_path and os.path.exists(kubeconfig_path):
            try:
                os.remove(kubeconfig_path)
                logger.debug(f"Temporary kubeconfig file '{kubeconfig_path}' removed.")
            except OSError as e:
                logger.error(f"Error removing temporary kubeconfig file '{kubeconfig_path}': {e}", exc_info=True)
    return success_flag


def scale_deployment(deployment_name: str, namespace: str, replicas: int, cluster_name: str) -> bool:
    """
    Scales a Kubernetes deployment to the specified number of replicas in the given Kind cluster.
    """
    logger.info(f"Scaling deployment '{deployment_name}' in namespace '{namespace}' to {replicas} replicas in cluster '{cluster_name}'.")
    kubeconfig_path = _get_kind_kubeconfig(cluster_name)
    if not kubeconfig_path:
        return False

    success_flag = False
    try:
        scale_command = [
            'scale', 'deployment', deployment_name,
            f'--replicas={replicas}',
            '--namespace', namespace
        ]
        process = _run_kubectl_command(scale_command, kubeconfig_path)
        if process.returncode == 0:
            logger.info(f"Deployment '{deployment_name}' in namespace '{namespace}' scaled to {replicas} replicas successfully.\n{process.stdout.strip()}")
            success_flag = True
        else:
            logger.error(f"Failed to scale deployment '{deployment_name}'. Error:\n{process.stderr.strip()}")
            success_flag = False
    finally:
        if kubeconfig_path and os.path.exists(kubeconfig_path):
            try:
                os.remove(kubeconfig_path)
                logger.debug(f"Temporary kubeconfig file '{kubeconfig_path}' removed.")
            except OSError as e:
                logger.error(f"Error removing temporary kubeconfig file '{kubeconfig_path}': {e}", exc_info=True)
    return success_flag
