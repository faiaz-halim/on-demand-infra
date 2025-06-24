import subprocess
import logging
import os
import shutil
import tempfile
import time
from typing import Optional, List

# Configure logger
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
        # Log stdout only if not excessively long
        stdout_log = process.stdout.strip()
        if len(stdout_log) > 1000:
            stdout_log = stdout_log[:500] + "... (truncated)"
        logger.debug(f"Command '{' '.join(command)}' stdout:\n{stdout_log}")

        if process.stderr.strip():
            logger.debug(f"Command '{' '.join(command)}' stderr:\n{process.stderr.strip()}")
        return process
    except FileNotFoundError:
        logger.error(f"Command not found: {command[0]}")
        return subprocess.CompletedProcess(command, -1, stdout="", stderr=f"Command not found: {command[0]}")
    except Exception as e:
        logger.error(f"Exception running command: {str(e)}", exc_info=True)
        return subprocess.CompletedProcess(command, -1, stdout="", stderr=str(e))

def detect_kind_cluster(cluster_name: str) -> bool:
    """Detects if a Kind cluster is running."""
    logger.info(f"Detecting Kind cluster: {cluster_name}")
    kind_path = shutil.which('kind')
    if not kind_path:
        logger.error("kind command not found in PATH")
        return False

    command = [kind_path, 'get', 'clusters']
    process = _run_command(command)
    return cluster_name in process.stdout.splitlines() if process.returncode == 0 else False

def apply_calico(cluster_name: str, calico_yaml_url: str) -> bool:
    """Applies Calico CNI to the specified Kind cluster."""
    logger.info(f"Applying Calico CNI to {cluster_name}")
    kubectl_path = shutil.which('kubectl')
    kind_path = shutil.which('kind')
    if not kubectl_path or not kind_path:
        logger.error("kubectl or kind not found in PATH")
        return False

    # Export kubeconfig
    kubeconfig_cmd = [kind_path, 'export', 'kubeconfig', '--name', cluster_name]
    kubeconfig_process = _run_command(kubeconfig_cmd)
    if kubeconfig_process.returncode != 0:
        logger.error(f"Failed to export kubeconfig: {kubeconfig_process.stderr}")
        return False

    # Apply Calico
    apply_cmd = [kubectl_path, 'apply', '-f', calico_yaml_url]
    apply_process = _run_command(apply_cmd)
    return apply_process.returncode == 0

def wait_for_api_server(cluster_name: str, timeout: int = 300) -> bool:
    """Waits for API server readiness with exponential backoff."""
    kubectl_path = shutil.which('kubectl')
    if not kubectl_path:
        logger.error("kubectl not found")
        return False

    start_time = time.time()
    wait_interval = 2
    max_interval = 30
    retry_count = 0

    while time.time() - start_time < timeout:
        retry_count += 1
        process = _run_command([kubectl_path, 'cluster-info'])

        if process.returncode == 0:
            logger.info("API server is ready")
            return True

        logger.warning(f"API server not ready (attempt {retry_count}), retrying in {wait_interval}s")
        time.sleep(wait_interval)
        wait_interval = min(wait_interval * 2, max_interval)

    logger.error("Timeout waiting for API server")
    return False

def create_kind_cluster(
    cluster_name: str,
    config_path: Optional[str] = None,
    calico_yaml_url: Optional[str] = None
) -> bool:
    """Creates a Kind cluster with proper readiness checks."""
    if detect_kind_cluster(cluster_name):
        logger.info(f"Cluster {cluster_name} already exists")
        return True

    kind_path = shutil.which('kind')
    if not kind_path:
        logger.error("kind not found in PATH")
        return False

    # Build create command
    cmd = [kind_path, 'create', 'cluster', '--name', cluster_name]
    if config_path:
        cmd.extend(['--config', config_path])

    # Create cluster
    process = _run_command(cmd)
    if process.returncode != 0:
        logger.error(f"Cluster creation failed: {process.stderr}")
        return False

    # Wait for API server
    if not wait_for_api_server(cluster_name):
        logger.error("API server not ready after cluster creation")
        return False

    # Apply Calico if needed
    if calico_yaml_url:
        if not apply_calico(cluster_name, calico_yaml_url):
            logger.error("Calico installation failed")
            return False

    return True

def delete_kind_cluster(cluster_name: str) -> bool:
    """Deletes a Kind cluster."""
    kind_path = shutil.which('kind')
    if not kind_path:
        logger.error("kind not found in PATH")
        return False

    cmd = [kind_path, 'delete', 'cluster', '--name', cluster_name]
    process = _run_command(cmd)
    return process.returncode == 0

def load_image_into_kind(image_name_tag: str, cluster_name: str) -> bool:
    """Loads Docker image into Kind cluster."""
    kind_path = shutil.which('kind')
    if not kind_path:
        logger.error("kind not found in PATH")
        return False

    cmd = [kind_path, 'load', 'docker-image', image_name_tag, '--name', cluster_name]
    process = _run_command(cmd)
    return process.returncode == 0
