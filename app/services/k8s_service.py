import subprocess
import logging
import os
import shutil
import pathlib
from typing import Optional, List, Dict, Tuple, Any
import json
import yaml

logger = logging.getLogger(__name__)

def _run_kubectl_command(command: List[str], kubeconfig_path: str) -> subprocess.CompletedProcess:
    if not kubeconfig_path or not os.path.exists(kubeconfig_path):
        logger.error(f"Invalid kubeconfig: {kubeconfig_path}")
        return subprocess.CompletedProcess(command, 1, "", "Invalid kubeconfig")

    kubectl = shutil.which('kubectl')
    if not kubectl:
        logger.error("kubectl not found")
        return subprocess.CompletedProcess(command, 1, "", "kubectl not found")

    env = os.environ.copy()
    env["KUBECONFIG"] = kubeconfig_path

    try:
        return subprocess.run(
            [kubectl] + command,
            capture_output=True,
            text=True,
            check=False,
            env=env
        )
    except Exception as e:
        logger.error(f"kubectl error: {str(e)}")
        return subprocess.CompletedProcess(command, 1, "", str(e))

def create_namespace(namespace: str, kubeconfig_path: str) -> Tuple[bool, str]:
    """
    Create a namespace if it doesn't exist

    Returns:
        Tuple[bool, str]: (success status, message)
    """
    result = _run_kubectl_command(['get', 'namespace', namespace], kubeconfig_path)
    if result.returncode == 0:
        msg = f"Namespace {namespace} exists"
        logger.info(msg)
        return True, msg

    result = _run_kubectl_command(['create', 'namespace', namespace], kubeconfig_path)
    if result.returncode == 0:
        msg = f"Namespace {namespace} created"
        logger.info(msg)
        return True, msg
    else:
        error_msg = f"Failed to create namespace {namespace}: {result.stderr}"
        logger.error(error_msg)
        return False, error_msg

def apply_manifests(manifest_path: str, kubeconfig_path: str, namespace: str) -> bool:
    result = _run_kubectl_command(['apply', '-f', manifest_path, '-n', namespace], kubeconfig_path)
    return result.returncode == 0

def get_service_info(service_name: str, namespace: str, kubeconfig_path: str) -> Optional[Dict]:
    result = _run_kubectl_command(['get', 'service', service_name, '-n', namespace, '-o', 'json'], kubeconfig_path)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

def generate_eks_kubeconfig_file(cluster_name: str, endpoint: str, ca_data: str, region: str, role_arn: Optional[str], output_dir: str) -> Optional[str]:
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)
    kubeconfig_path = pathlib.Path(output_dir) / f"kubeconfig_{cluster_name}.yaml"

    config = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{"name": cluster_name, "cluster": {"server": endpoint, "certificate-authority-data": ca_data}}],
        "contexts": [{"name": cluster_name, "context": {"cluster": cluster_name, "user": cluster_name}}],
        "current-context": cluster_name,
        "users": [{"name": cluster_name, "user": {"exec": {
            "apiVersion": "client.authentication.k8s.io/v1beta1",
            "command": "aws",
            "args": ["eks", "get-token", "--cluster-name", cluster_name] + (["--role-arn", role_arn] if role_arn else [])
        }}}]
    }

    try:
        with open(kubeconfig_path, 'w') as f:
            yaml.dump(config, f)
        return str(kubeconfig_path)
    except Exception as e:
        logger.error(f"Failed to write kubeconfig: {str(e)}")
        return None

def install_nginx_ingress_helm(kubeconfig_path: str, namespace: str = "ingress-nginx", version: str = "4.10.0") -> bool:
    helm = shutil.which('helm')
    if not helm:
        logger.error("Helm not found")
        return False

    env = os.environ.copy()
    env["KUBECONFIG"] = kubeconfig_path

    # Add repo
    repo_cmd = [helm, 'repo', 'add', 'ingress-nginx', 'https://kubernetes.github.io/ingress-nginx']
    repo_result = subprocess.run(repo_cmd, capture_output=True, text=True, env=env)
    if repo_result.returncode != 0 and "already exists" not in repo_result.stderr:
        logger.error(f"Failed to add repo: {repo_result.stderr}")
        return False

    # Update repos
    update_cmd = [helm, 'repo', 'update']
    update_result = subprocess.run(update_cmd, capture_output=True, text=True, env=env)
    if update_result.returncode != 0:
        logger.error(f"Failed to update repos: {update_result.stderr}")
        return False

    # Install chart
    install_cmd = [
        helm, 'upgrade', '--install', 'ingress-nginx', 'ingress-nginx/ingress-nginx',
        '--namespace', namespace,
        '--create-namespace',
        '--version', version,
        '--set', 'controller.service.type=LoadBalancer'
    ]
    install_result = subprocess.run(install_cmd, capture_output=True, text=True, env=env)
    return install_result.returncode == 0

def get_load_balancer_details(service_name: str, namespace: str, kubeconfig_path: str) -> Optional[Dict]:
    """
    Get details of a LoadBalancer service including the external IP or hostname.

    Args:
        service_name: Name of the service
        namespace: Namespace of the service
        kubeconfig_path: Path to the kubeconfig file

    Returns:
        Dictionary containing service details, or None if not found
    """
    service_info = get_service_info(service_name, namespace, kubeconfig_path)
    if not service_info:
        return None

    # Extract load balancer details
    lb_details = {
        "name": service_info.get("metadata", {}).get("name"),
        "namespace": service_info.get("metadata", {}).get("namespace"),
        "type": service_info.get("spec", {}).get("type"),
        "cluster_ip": service_info.get("spec", {}).get("clusterIP"),
        "external_ips": service_info.get("spec", {}).get("externalIPs", []),
        "ports": service_info.get("spec", {}).get("ports", []),
    }

    # For LoadBalancer services, get the ingress details
    status = service_info.get("status", {}).get("loadBalancer", {})
    if status:
        lb_details["ingress"] = status.get("ingress", [])

    return lb_details
