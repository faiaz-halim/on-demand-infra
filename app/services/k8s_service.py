import subprocess
import logging
import os
import shutil
import tempfile
import pathlib
from typing import Optional, List, Dict, Tuple, Any # Added Tuple, Any
import time # Added
import json # Added

import yaml
import base64

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


def get_load_balancer_details(
    kubeconfig_path: str,
    service_name: str,
    namespace: str,
    timeout_seconds: int = 300 # Wait up to 5 mins for LB to get an address
) -> Optional[Tuple[str, str]]: # (hostname, hosted_zone_id - may be None for NLB)
    logger.info(f"Attempting to get LoadBalancer details for service '{service_name}' in namespace '{namespace}' using kubeconfig '{kubeconfig_path}'. Timeout: {timeout_seconds}s.")

    # Ensure kubectl is available (checked by _run_kubectl_command)
    # shutil.which("kubectl") is implicitly checked by _run_kubectl_command's helper part,
    # but if _run_kubectl_command is not used, direct check is good.
    # The current _run_kubectl_command already handles this.

    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        # Use -o jsonpath for more direct extraction if preferred, but full JSON gives more context.
        cmd = ['get', 'service', service_name, '-n', namespace, '-o', 'json']
        result = _run_kubectl_command(cmd, kubeconfig_path) # Uses the helper that sets KUBECONFIG

        if result.returncode == 0:
            try:
                service_info = json.loads(result.stdout)
                ingresses = service_info.get("status", {}).get("loadBalancer", {}).get("ingress")
                if ingresses and len(ingresses) > 0:
                    hostname = ingresses[0].get("hostname")
                    # For NLBs, the canonical hosted zone ID is not available via kubectl.
                    # It needs to be fetched from AWS API (e.g. elbv2.describe_load_balancers).
                    # This function will return None for hosted_zone_id in NLB case from kubectl.
                    # The caller (orchestrator) will need a separate step to get NLB's canonical HZID if needed for Route53 alias.
                    if hostname:
                        logger.info(f"Found LoadBalancer hostname: {hostname} for service {service_name}/{namespace}")
                        return hostname, None # Returning None for nlb_hosted_zone_id from kubectl
                    else: # Hostname might be empty string temporarily
                        logger.info(f"LoadBalancer ingress found, but hostname is not yet available for {service_name}/{namespace}. Retrying...")
                else:
                    logger.info(f"LoadBalancer ingress not yet available for {service_name}/{namespace}. Retrying...")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse JSON output from kubectl get service: {result.stdout}")
                # Don't retry on JSON parse error, it's likely a persistent issue with output.
                return None
            except Exception as e: # Includes potential KeyError if structure is unexpected
                logger.error(f"Error processing service info for {service_name}/{namespace}: {e}", exc_info=True)
                return None # Don't retry on unexpected parsing error.
        else:
            logger.warning(f"Failed to get service {service_name}/{namespace} (kubectl exit code {result.returncode}). Retrying... Stderr: {result.stderr}")

        time.sleep(15) # Wait before retrying

    logger.error(f"Timeout waiting for LoadBalancer details for service {service_name}/{namespace}.")
    return None


def generate_eks_kubeconfig_file(
    cluster_name: str,
    endpoint_url: str,
    ca_data: str, # Base64 encoded CA data from EKS output
    aws_region: str,
    user_arn: Optional[str], # Optional: if mapping a specific IAM user/role
    output_dir: str # Directory to save the kubeconfig file
) -> Optional[str]:
    logger.info(f"Generating kubeconfig file for EKS cluster '{cluster_name}' in dir '{output_dir}'")

    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)
    kubeconfig_path = pathlib.Path(output_dir) / f"kubeconfig_{cluster_name}.yaml"

    # Using a simplified kubeconfig structure that relies on aws-iam-authenticator compatible token generation
    # The complex ARN format for cluster/user/context names is not strictly necessary if names are unique.
    kubeconfig_content = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [{
            "name": cluster_name, # Simplified cluster name
            "cluster": {
                "server": endpoint_url,
                "certificate-authority-data": ca_data # Already base64 from EKS output
            }
        }],
        "contexts": [{
            "name": cluster_name, # Simplified context name
            "context": {
                "cluster": cluster_name,
                "user": cluster_name # Simplified user name, matches context
            }
        }],
        "current-context": cluster_name,
        "users": [{
            "name": cluster_name, # Simplified user name
            "user": {
                "exec": {
                    "apiVersion": "client.authentication.k8s.io/v1beta1",
                    "command": "aws",
                    "args": [
                        "eks", "get-token", # Removed --region from here, should be picked from env or profile
                        "--cluster-name", cluster_name,
                    ],
                    # Env vars for AWS CLI should be set in the environment where this command runs
                    # (e.g., the environment of the MCP server itself if it has AWS CLI and credentials)
                    # "env": [
                    #     {"name": "AWS_REGION", "value": aws_region},
                    # ] # AWS_REGION is often picked up by aws cli from env.
                }
            }
        }]
    }
    # If a specific role ARN is provided for authentication, add it to the exec args.
    if user_arn:
         kubeconfig_content["users"][0]["user"]["exec"]["args"].extend(["--role-arn", user_arn])

    # If aws_region is specified and not already in global env for CLI, it can be added to exec args.
    # However, it's often better if the execution environment for `aws` CLI is already configured with a default region.
    # Forcing it here can be an option if needed:
    # kubeconfig_content["users"][0]["user"]["exec"]["args"].extend(["--region", aws_region])
    # For now, assuming region is configured in the AWS CLI environment or profile.


    try:
        with open(kubeconfig_path, "w") as f:
            yaml.dump(kubeconfig_content, f)
        logger.info(f"Kubeconfig for EKS cluster '{cluster_name}' saved to {kubeconfig_path}")
        return str(kubeconfig_path)
    except Exception as e:
        logger.error(f"Failed to write kubeconfig file for EKS cluster '{cluster_name}': {e}", exc_info=True)
        return None

def install_nginx_ingress_helm(
    kubeconfig_path: str,
    namespace: str = "ingress-nginx",
    helm_chart_version: str = "4.10.0",
    values_override: Optional[Dict[str, Any]] = None
) -> bool:
    logger.info(f"Installing Nginx Ingress Controller via Helm into namespace '{namespace}' using kubeconfig '{kubeconfig_path}'")

    helm_path = shutil.which("helm")
    if not helm_path:
        logger.error("Helm CLI not found. Cannot install Nginx Ingress Controller.")
        return False

    env_with_kubeconfig = os.environ.copy()
    env_with_kubeconfig["KUBECONFIG"] = kubeconfig_path

    # Add Helm repo
    repo_add_cmd = [helm_path, "repo", "add", "ingress-nginx", "https://kubernetes.github.io/ingress-nginx", "--force-update"]
    logger.info(f"Running Helm command: {' '.join(repo_add_cmd)}")
    repo_add_result = subprocess.run(repo_add_cmd, capture_output=True, text=True, check=False, env=env_with_kubeconfig)
    if repo_add_result.returncode != 0:
        # "already exists" is not an error for repo add if we ensure it's up-to-date
        if "already exists" in repo_add_result.stderr.lower():
            logger.info("Nginx Ingress Helm repo already added.")
        else:
            logger.error(f"Failed to add Nginx Ingress Helm repo. Stderr: {repo_add_result.stderr.strip()}")
            return False
    else:
        logger.info("Nginx Ingress Helm repo added successfully.")

    # Update Helm repos
    repo_update_cmd = [helm_path, "repo", "update"]
    logger.info(f"Running Helm command: {' '.join(repo_update_cmd)}")
    repo_update_result = subprocess.run(repo_update_cmd, capture_output=True, text=True, check=False, env=env_with_kubeconfig)
    if repo_update_result.returncode != 0:
        logger.error(f"Failed to update Helm repos. Stderr: {repo_update_result.stderr.strip()}")
        return False
    logger.info("Helm repos updated successfully.")

    # Prepare Helm install command
    # Using `helm upgrade --install` for idempotency
    install_cmd = [
        helm_path, "upgrade", "--install", "ingress-nginx", "ingress-nginx/ingress-nginx",
        "--namespace", namespace,
        "--create-namespace",
        "--version", helm_chart_version,
        "--set", "controller.service.type=LoadBalancer",
        # Consider adding --wait for helm to wait for resources to be ready, though it can be long
    ]
    if values_override:
        for key, value in values_override.items():
            install_cmd.extend(["--set", f"{key}={str(value)}"]) # Ensure value is string for --set

    logger.info(f"Running Helm install/upgrade command: {' '.join(install_cmd)}")
    install_result = subprocess.run(install_cmd, capture_output=True, text=True, check=False, env=env_with_kubeconfig)

    if install_result.returncode != 0:
        logger.error(f"Failed to install/upgrade Nginx Ingress. Stderr: {install_result.stderr.strip()} Stdout: {install_result.stdout.strip()}")
        # Even if it fails, check status in case it was a transient issue or misconfiguration but deployment exists
        status_cmd = [helm_path, "status", "ingress-nginx", "-n", namespace]
        status_result = subprocess.run(status_cmd, capture_output=True, text=True, check=False, env=env_with_kubeconfig)
        if status_result.returncode == 0 and "STATUS: deployed" in status_result.stdout:
            logger.warning("Helm install/upgrade failed, but Nginx Ingress Controller seems to be deployed. Proceeding with caution.")
            return True
        return False

    logger.info(f"Nginx Ingress Controller installed/upgraded successfully via Helm. Output:\n{install_result.stdout.strip()}")
    return True


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
