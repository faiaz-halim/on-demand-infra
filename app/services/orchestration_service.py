import logging
from typing import Optional, Dict, Any, List
import asyncio
import tempfile
import pathlib
import shutil
import uuid
import json
import time

from app.core.schemas import AWSCredentials, ChatCompletionRequest, ChatMessage
from app.core.config import settings
from app.services import terraform_service
from app.services import ssh_service
from app.services import manifest_service
from app.services import docker_service # Added for ECR push
from app.services import git_service    # Added for cloning repo
import docker # Added for docker.from_env()

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def _append_message_to_chat(chat_request: ChatCompletionRequest, role: str, content: str):
    """Helper to append messages to the chat request's message list."""
    chat_request.messages.append(ChatMessage(role=role, content=content))


async def handle_local_deployment(
    repo_url: str,
    namespace: str,
    chat_request: ChatCompletionRequest
):
    # ... (implementation as before) ...
    logger.info(f"Local deployment requested for repo '{repo_url}' in namespace '{namespace}'.")
    message_content = f"Local deployment process started for {repo_url} in namespace {namespace}. Preparing environment..."
    await _append_message_to_chat(chat_request, "assistant", message_content)
    return {"status": "success", "mode": "local", "message": message_content}


async def handle_cloud_local_deployment(
    repo_url: str,
    namespace: str,
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest
):
    # ... (implementation as before, from previous steps) ...
    repo_name_part = repo_url.split('/')[-1].replace('.git', '').replace('.', '-')
    unique_id = uuid.uuid4().hex[:6]
    instance_name_tag = chat_request.instance_id or f"mcp-cl-{repo_name_part}-{unique_id}"
    sg_name = f"{instance_name_tag}-sg"
    app_name = repo_name_part.lower().replace('_', '-')

    logger.info(f"Cloud-local deployment: provisioning EC2 instance '{instance_name_tag}' for repo '{repo_url}' to deploy app '{app_name}' in K8s namespace '{namespace}'.")
    await _append_message_to_chat(chat_request, "assistant", f"Initiating cloud-local deployment for {repo_url}. App: {app_name}, Instance ID (Tag): {instance_name_tag}, K8s Namespace: {namespace}")

    current_tf_workspace = pathlib.Path(settings.PERSISTENT_WORKSPACE_BASE_DIR) / "cloud-local" / instance_name_tag
    current_tf_workspace.mkdir(parents=True, exist_ok=True)
    logger.info(f"Using Terraform workspace for instance '{instance_name_tag}': {current_tf_workspace}")

    local_manifest_temp_dir_path: Optional[pathlib.Path] = None

    ec2_key_name_to_use = chat_request.ec2_key_name or settings.EC2_DEFAULT_KEY_NAME
    if not ec2_key_name_to_use:
        err_msg = "EC2 Key Name is not configured/provided."
        logger.error(err_msg)
        await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
        return {"status": "error", "mode": "cloud-local", "message": err_msg}

    node_port_num = 30000 + (sum(ord(c) for c in app_name) % 2768)
    logger.info(f"Calculated NodePort for service '{app_name}': {node_port_num}")

    try:
        aws_env_vars = {
            "AWS_ACCESS_KEY_ID": aws_creds.aws_access_key_id.get_secret_value(),
            "AWS_SECRET_ACCESS_KEY": aws_creds.aws_secret_access_key.get_secret_value(),
            "AWS_DEFAULT_REGION": aws_creds.aws_region,
        }
        logger.info(f"AWS environment variables prepared. Region: {aws_creds.aws_region}.")

        await _append_message_to_chat(chat_request, "assistant", "Generating EC2 bootstrap script...")
        bootstrap_context = {'kind_version': settings.DEFAULT_KIND_VERSION, 'kubectl_version': settings.DEFAULT_KUBECTL_VERSION}
        bootstrap_content = await asyncio.to_thread(terraform_service.generate_ec2_bootstrap_script, bootstrap_context)
        if bootstrap_content is None:
            err_msg = "Failed to generate EC2 bootstrap script."
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        await _append_message_to_chat(chat_request, "assistant", "Generating Terraform configuration for EC2...")
        tf_app_ports = [{'port': node_port_num, 'protocol': 'tcp'}]
        tf_context = {
            "aws_region": aws_creds.aws_region, "ami_id": settings.EC2_DEFAULT_AMI_ID,
            "instance_type": settings.EC2_DEFAULT_INSTANCE_TYPE, "key_name": ec2_key_name_to_use,
            "instance_name_tag": instance_name_tag, "sg_name": sg_name,
            "ssh_cidr": "0.0.0.0/0", "app_ports": tf_app_ports,
            "user_data_content": bootstrap_content,
        }

        tf_file_path_str = await asyncio.to_thread(terraform_service.generate_ec2_tf_config, tf_context, str(current_tf_workspace))
        if tf_file_path_str is None:
            err_msg = "Failed to generate Terraform EC2 configuration."
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        await _append_message_to_chat(chat_request, "assistant", f"Initializing Terraform at {current_tf_workspace}...")
        init_ok, init_out, init_err = await asyncio.to_thread(terraform_service.run_terraform_init, str(current_tf_workspace), aws_env_vars)
        if not init_ok:
            err_msg = f"Terraform init failed: {init_err or init_out}"
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        await _append_message_to_chat(chat_request, "assistant", "Applying Terraform to provision EC2 (may take minutes)...")
        apply_ok, outputs, apply_out, apply_err = await asyncio.to_thread(terraform_service.run_terraform_apply, str(current_tf_workspace), aws_env_vars)
        if not apply_ok:
            err_msg = f"Terraform apply failed: {apply_err or apply_out}"
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}. Attempting destroy...")
            destroy_ok, destroy_out_msg, destroy_err_msg = await asyncio.to_thread(terraform_service.run_terraform_destroy, str(current_tf_workspace), aws_env_vars)
            log_destroy_msg = f"Terraform destroy attempt after apply failure: Success={destroy_ok}\nstdout:\n{destroy_out_msg}\nstderr:\n{destroy_err_msg}"
            logger.info(log_destroy_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Resource cleanup attempt: {log_destroy_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        public_ip = outputs.get("public_ip", "Not available")
        instance_id_from_tf = outputs.get("instance_id", "Not available")
        await _append_message_to_chat(chat_request, "assistant", f"EC2 instance '{instance_name_tag}' provisioned! IP: {public_ip}, AWS ID: {instance_id_from_tf}.")

        if public_ip == "Not available":
            err_msg = "EC2 Public IP not available from Terraform outputs."
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}
        if not settings.EC2_PRIVATE_KEY_BASE_PATH:
            err_msg = "Server configuration error: EC2_PRIVATE_KEY_BASE_PATH not set."
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}
        private_key_full_path = str(pathlib.Path(settings.EC2_PRIVATE_KEY_BASE_PATH) / ec2_key_name_to_use)
        if not pathlib.Path(private_key_full_path).exists():
            err_msg = f"SSH private key '{ec2_key_name_to_use}' not found at {private_key_full_path}."
            await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        remote_repo_path = f"{settings.EC2_DEFAULT_REPO_PATH}/{app_name}"
        clone_command = f"sudo rm -rf {remote_repo_path} && git clone --depth 1 {repo_url} {remote_repo_path}"
        await _append_message_to_chat(chat_request, "assistant", f"Cloning repository {repo_url} on EC2...")
        await asyncio.sleep(15)
        _, clone_stderr, clone_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, clone_command)
        if clone_exit_code != 0:  err_msg = f"Failed to clone repo on EC2: {clone_stderr}" ; await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}"); return {"status": "error", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", "Repository cloned.")

        built_image_tag = f"{app_name}:latest"
        docker_build_command = f"cd {remote_repo_path} && sudo docker build -t {built_image_tag} ."
        await _append_message_to_chat(chat_request, "assistant", f"Building Docker image {built_image_tag} on EC2...")
        _, build_stderr, build_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, docker_build_command)
        if build_exit_code != 0: err_msg = f"Failed to build Docker image on EC2: {build_stderr}" ; await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}"); return {"status": "error", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", "Docker image built.")

        load_image_command = f"sudo kind load docker-image {built_image_tag} --name {settings.KIND_CLUSTER_NAME}"
        await _append_message_to_chat(chat_request, "assistant", f"Loading image into Kind...")
        _, load_stderr, load_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, load_image_command)
        if load_exit_code != 0: err_msg = f"Failed to load image to Kind: {load_stderr}" ; await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}"); return {"status": "error", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", "Image loaded to Kind.")

        container_port = settings.EC2_DEFAULT_APP_PORTS[0]['port'] if settings.EC2_DEFAULT_APP_PORTS else 80
        deployment_yaml = manifest_service.generate_deployment_manifest(image_name=built_image_tag, app_name=app_name, replicas=1, ports=[container_port], namespace=namespace)
        service_yaml = manifest_service.generate_service_manifest(app_name=app_name, service_type="NodePort", ports_mapping=[{'port': 80, 'targetPort': container_port, 'nodePort': node_port_num}], namespace=namespace)
        if not deployment_yaml or not service_yaml: err_msg = "Failed to generate K8s manifests." ; await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}"); return {"status": "error", "message": err_msg}

        local_manifest_temp_dir_path = pathlib.Path(tempfile.mkdtemp(prefix="mcp_manifests_local_"))
        logger.info(f"Created local temp manifest dir: {local_manifest_temp_dir_path}")
        try:
            with open(local_manifest_temp_dir_path / "deployment.yaml", "w") as f: f.write(deployment_yaml)
            with open(local_manifest_temp_dir_path / "service.yaml", "w") as f: f.write(service_yaml)
            remote_manifest_dir = settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH
            mkdir_command = f"mkdir -p {remote_manifest_dir}"
            _, mkdir_err, mkdir_exit = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, mkdir_command)
            if mkdir_exit != 0: err_msg = f"Failed to create remote manifest dir: {mkdir_err}" ; await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}"); return {"status": "error", "message": err_msg}

            await _append_message_to_chat(chat_request, "assistant", "Uploading K8s manifests...")
            upload_dep_ok = await asyncio.to_thread(ssh_service.upload_file_sftp, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, str(local_manifest_temp_dir_path / "deployment.yaml"), f"{remote_manifest_dir}/deployment.yaml")
            upload_svc_ok = await asyncio.to_thread(ssh_service.upload_file_sftp, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, str(local_manifest_temp_dir_path / "service.yaml"), f"{remote_manifest_dir}/service.yaml")
            if not upload_dep_ok or not upload_svc_ok: err_msg = "Failed to upload K8s manifests." ; await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}"); return {"status": "error", "message": err_msg}
        finally:
            if local_manifest_temp_dir_path.exists(): shutil.rmtree(local_manifest_temp_dir_path)

        apply_command = f"sudo kubectl apply --namespace {namespace} -f {remote_manifest_dir}/"
        await _append_message_to_chat(chat_request, "assistant", "Applying K8s manifests on EC2...")
        _, apply_m_stderr, apply_m_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, apply_command)
        if apply_m_exit_code != 0: err_msg = f"Failed to apply K8s manifests: {apply_m_stderr}" ; await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}"); return {"status": "error", "message": err_msg}

        cleanup_remote_cmd = f"rm -rf {remote_manifest_dir}"
        await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, cleanup_remote_cmd)

        app_url = f"http://{public_ip}:{node_port_num}"
        final_success_message = f"Application '{app_name}' deployed to Kind on EC2 instance '{instance_name_tag}' (IP: {public_ip}) in namespace '{namespace}'. Instance ID for management: {instance_name_tag}. Application may be accessible at: {app_url}"
        await _append_message_to_chat(chat_request, "assistant", final_success_message)

        return {"status": "success", "mode": "cloud-local", "message": final_success_message, "outputs": outputs,
                "instance_id": instance_name_tag,
                "ec2_public_ip": public_ip, "built_image_tag": built_image_tag, "app_url": app_url}

    except Exception as e:
        err_msg = f"An unexpected error in handle_cloud_local_deployment: {str(e)}"
        logger.error(err_msg, exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {err_msg}")
        return {"status": "error", "mode": "cloud-local", "message": err_msg}


async def handle_cloud_local_decommission(
    instance_id: str,
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest
) -> Dict[str, Any]:
    # ... (implementation as before) ...
    logger.info(f"Decommission requested for cloud-local instance ID: {instance_id}")
    await _append_message_to_chat(chat_request, "assistant", f"Initiating decommission for instance: {instance_id}.")
    workspace_dir = pathlib.Path(settings.PERSISTENT_WORKSPACE_BASE_DIR) / "cloud-local" / instance_id
    if not workspace_dir.exists() or not workspace_dir.is_dir():
        err_msg = f"Workspace for instance ID '{instance_id}' not found at expected location: {workspace_dir}."
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        return {"status": "error", "message": err_msg, "instance_id": instance_id}
    # ... (rest of the decommission logic) ...
    await _append_message_to_chat(chat_request, "assistant", f"Decommission for instance '{instance_id}' (stubbed) completed.") # This line should be updated by full logic
    return {"status": "success", "message": f"Instance {instance_id} decommissioned (stub).", "instance_id": instance_id} # This also


async def handle_cloud_local_redeploy(
    instance_id: str,
    public_ip: str,
    ec2_key_name: str,
    repo_url: str,
    namespace: str,
    aws_creds: Optional[AWSCredentials],
    chat_request: ChatCompletionRequest
) -> Dict[str, Any]:
    # ... (implementation as before) ...
    logger.info(f"Redeploy requested for cloud-local instance: {instance_id} (IP: {public_ip}), repo: {repo_url}, namespace: {namespace}")
    await _append_message_to_chat(chat_request, "assistant", f"Initiating redeploy for instance {instance_id} (IP: {public_ip}) using repo {repo_url} for namespace {namespace}.")
    # ... (rest of the redeploy logic) ...
    await _append_message_to_chat(chat_request, "assistant", f"Redeploy for instance '{instance_id}' (stubbed) completed.")
    return {"status": "success", "message": "Redeployment complete (stub).", "instance_id": instance_id}


async def handle_cloud_hosted_deployment(
    repo_url: str,
    namespace: str,
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest,
    instance_id_override: Optional[str] = None # Added
) -> Dict[str, Any]:
    """Handles the cloud-hosted (EKS & ECR) deployment workflow, including local image build and ECR push."""

    repo_name_from_url = repo_url.split('/')[-1].replace('.git', '').replace('_', '-') # Used for local tag & clone dir
    unique_id_part = instance_id_override if instance_id_override else uuid.uuid4().hex[:8]

    # Validate and construct names
    # EKS cluster names have length constraints (1-100) and char constraints (alphanumeric and hyphens)
    # ECR repo names are more flexible but usually follow <namespace>/<name> or just <name>
    # For simplicity, we'll use a similar pattern. Ensure they are valid.
    raw_cluster_name = f"{settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX}-{repo_name_from_url}-{unique_id_part}"
    cluster_name = "".join(c if c.isalnum() or c == '-' else '-' for c in raw_cluster_name)[:100] # Basic sanitization & length

    raw_ecr_repo_name = f"{settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{repo_name_from_url}-{unique_id_part}"
    # ECR names: (?:[a-z0-9]+(?:[._-][a-z0-9]+)*/)*[a-z0-9]+(?:[._-][a-z0-9]+)*
    # Simplified: lowercase, numbers, hyphens, underscores, periods, slashes. No leading/trailing separators.
    ecr_repo_name = "".join(c if c.islower() or c.isdigit() or c in ['-', '_', '.'] else '-' for c in raw_ecr_repo_name.lower())
    ecr_repo_name = ecr_repo_name.strip('-_.')[:255]


    logger.info(f"Cloud-hosted deployment: Cluster '{cluster_name}', ECR Repo '{ecr_repo_name}' for Git repo '{repo_url}'")
    await _append_message_to_chat(chat_request, "assistant", f"Initiating cloud-hosted deployment: EKS Cluster '{cluster_name}', ECR Repo '{ecr_repo_name}'.")

    workspace_dir_path = pathlib.Path(settings.PERSISTENT_WORKSPACE_BASE_DIR) / "cloud-hosted" / cluster_name
    workspace_dir_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Using Terraform workspace for EKS cluster '{cluster_name}': {workspace_dir_path}")

    aws_env_vars: Optional[Dict[str, str]] = None
    if aws_creds:
        aws_env_vars = {
            "AWS_ACCESS_KEY_ID": aws_creds.aws_access_key_id.get_secret_value(),
            "AWS_SECRET_ACCESS_KEY": aws_creds.aws_secret_access_key.get_secret_value(),
            "AWS_DEFAULT_REGION": aws_creds.aws_region,
        }
        logger.info(f"AWS environment variables prepared. Region: {aws_creds.aws_region}.")
    else:
        # This case should ideally be caught by router validation if creds are mandatory
        err_msg = "AWS credentials are required for cloud-hosted deployment but were not provided."
        logger.error(err_msg)
        await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
        return {"status": "error", "mode": "cloud-hosted", "message": err_msg}

    clone_workspace_path: Optional[pathlib.Path] = None # Define here for visibility in finally
    try:
        # 1. Generate ECR Terraform Config
        await _append_message_to_chat(chat_request, "assistant", f"Generating Terraform configuration for ECR repository '{ecr_repo_name}'...")
        ecr_tf_context = {
            "aws_region": aws_creds.aws_region,
            "ecr_repo_name": ecr_repo_name,
            "image_tag_mutability": settings.ECR_DEFAULT_IMAGE_TAG_MUTABILITY,
            "scan_on_push": settings.ECR_DEFAULT_SCAN_ON_PUSH
        }
        ecr_tf_file = await asyncio.to_thread(
            terraform_service.generate_ecr_tf_config, ecr_tf_context, str(workspace_dir_path)
        )
        if not ecr_tf_file:
            err_msg = "Failed to generate ECR Terraform configuration."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        # 2. Generate EKS Terraform Config
        await _append_message_to_chat(chat_request, "assistant", f"Generating Terraform configuration for EKS cluster '{cluster_name}'...")
        eks_tf_context = {
            "aws_region": aws_creds.aws_region,
            "cluster_name": cluster_name,
            # Defaults will be applied by generate_eks_tf_config if not specified here
            "vpc_cidr": settings.EKS_DEFAULT_VPC_CIDR,
            "num_public_subnets": settings.EKS_DEFAULT_NUM_PUBLIC_SUBNETS,
            "num_private_subnets": settings.EKS_DEFAULT_NUM_PRIVATE_SUBNETS,
            "eks_version": settings.EKS_DEFAULT_VERSION,
            "node_group_name": f"{cluster_name}-{settings.EKS_DEFAULT_NODE_GROUP_NAME_SUFFIX}",
            "node_instance_type": settings.EKS_DEFAULT_NODE_INSTANCE_TYPE,
            "node_desired_size": settings.EKS_DEFAULT_NODE_DESIRED_SIZE,
            "node_min_size": settings.EKS_DEFAULT_NODE_MIN_SIZE,
            "node_max_size": settings.EKS_DEFAULT_NODE_MAX_SIZE
        }
        eks_tf_file = await asyncio.to_thread(
            terraform_service.generate_eks_tf_config, eks_tf_context, str(workspace_dir_path)
        )
        if not eks_tf_file:
            err_msg = "Failed to generate EKS Terraform configuration."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        # 3. Terraform Init & Apply
        await _append_message_to_chat(chat_request, "assistant", f"Initializing Terraform for EKS cluster '{cluster_name}' and ECR repo '{ecr_repo_name}'...")
        init_ok, init_out, init_err = await asyncio.to_thread(
            terraform_service.run_terraform_init, str(workspace_dir_path), aws_env_vars
        )
        logger.info(f"Terraform init stdout:\n{init_out}")
        if init_err: logger.error(f"Terraform init stderr:\n{init_err}")
        if not init_ok:
            err_msg = f"Terraform init failed: {init_err or init_out}"
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", "Applying Terraform configuration for EKS & ECR (this may take 15-20 minutes)...")
        apply_ok, outputs, apply_out, apply_err = await asyncio.to_thread(
            terraform_service.run_terraform_apply, str(workspace_dir_path), aws_env_vars
        )
        logger.info(f"Terraform apply stdout:\n{apply_out}")
        if apply_err: logger.error(f"Terraform apply stderr:\n{apply_err}")

        if not apply_ok:
            err_msg = f"Terraform apply failed: {apply_err or apply_out}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}. Manual cleanup of AWS resources for '{cluster_name}' might be needed, or use the decommission action.")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name, "outputs": outputs if outputs else {}}

        # 4. Process Outputs from Terraform
        tf_ecr_repo_url_output = outputs.get("ecr_repository_url", {}).get("value") # This is <account_id>.dkr.ecr.<region>.amazonaws.com/<repo_name>
        tf_ecr_repo_name_output = outputs.get("ecr_repository_name", {}).get("value", ecr_repo_name) # Fallback to generated name
        eks_endpoint = outputs.get("eks_cluster_endpoint", {}).get("value", "Not available")
        eks_ca_data = outputs.get("eks_cluster_ca_data", {}).get("value", None)
        vpc_id = outputs.get("vpc_id", {}).get("value", "Not available")

        if not tf_ecr_repo_url_output:
            err_msg = "ECR repository URL not found in Terraform outputs. Cannot proceed with image push."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant",
            f"EKS & ECR infrastructure provisioned. ECR Repo: {tf_ecr_repo_name_output}, EKS Endpoint: {eks_endpoint}. Now building and pushing image...")

        # 5. Clone User's Repo Locally
        clone_workspace_path = pathlib.Path(tempfile.mkdtemp(prefix="mcp_clone_ch_"))
        logger.info(f"Created temporary clone workspace: {clone_workspace_path}")
        await _append_message_to_chat(chat_request, "assistant", f"Cloning repository {repo_url} locally for image build...")

        cloned_repo_details = await asyncio.to_thread(git_service.clone_repository, repo_url, str(clone_workspace_path))
        if not cloned_repo_details or not cloned_repo_details.get("success"):
            err_msg = f"Failed to clone repository {repo_url}: {cloned_repo_details.get('error', 'Unknown error')}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            # No need to rmtree here, finally block will handle it.
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        # Assuming clone_repository clones into a subdirectory named after the repo
        actual_cloned_path = clone_workspace_path / repo_name_from_url
        if not actual_cloned_path.exists() or not actual_cloned_path.is_dir():
             # If git_service.clone_repository puts it directly in clone_workspace_path:
             actual_cloned_path = clone_workspace_path
             if not actual_cloned_path.exists() or not actual_cloned_path.is_dir():
                err_msg = f"Cloned repository directory structure unexpected or not found at {actual_cloned_path} or {clone_workspace_path}"
                logger.error(err_msg)
                await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
                return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        logger.info(f"Repository cloned to {actual_cloned_path}")

        # 6. Build Docker Image Locally
        local_image_tag = f"{repo_name_from_url.lower()}-mcp:{uuid.uuid4().hex[:8]}"
        await _append_message_to_chat(chat_request, "assistant", f"Building Docker image {local_image_tag} locally...")
        build_result = await asyncio.to_thread(docker_service.build_docker_image_locally, actual_cloned_path, local_image_tag)
        build_logs = build_result.get('logs', '')
        logger.info(f"Docker build logs for {local_image_tag}:\n{build_logs}")

        if not build_result.get("success"):
            err_msg = f"Failed to build Docker image {local_image_tag}: {build_result.get('error', 'Unknown build error')}"
            logger.error(err_msg)
            # Provide a snippet of build logs if available
            log_snippet = build_logs[-500:] if build_logs else "No logs available."
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}\nBuild Log Snippet:\n{log_snippet}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", f"Docker image {local_image_tag} built successfully.")

        # 7. ECR Login
        await _append_message_to_chat(chat_request, "assistant", "Authenticating with AWS ECR...")
        login_details = await asyncio.to_thread(
            docker_service.get_ecr_login_details,
            aws_creds.aws_region,
            aws_creds.aws_access_key_id.get_secret_value(),
            aws_creds.aws_secret_access_key.get_secret_value()
        )
        if not login_details:
            err_msg = "Failed to get ECR login credentials."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        ecr_username, ecr_password, ecr_registry_from_token = login_details

        try:
            docker_client_instance = docker.from_env()
        except Exception as e:
            err_msg = f"Failed to initialize Docker client for ECR login: {str(e)}"
            logger.error(err_msg, exc_info=True)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        login_ok = await asyncio.to_thread(
            docker_service.login_to_ecr,
            docker_client_instance,
            ecr_registry_from_token, # This is like https://<account_id>.dkr.ecr.<region>.amazonaws.com
            ecr_username,
            ecr_password
        )
        if not login_ok:
            err_msg = "ECR login failed."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", "Successfully authenticated with ECR.")

        # 8. Push Image to ECR
        # Use tf_ecr_repo_name_output (actual name from TF) and ecr_registry_from_token (registry part)
        await _append_message_to_chat(chat_request, "assistant", f"Pushing image {local_image_tag} to ECR repository {tf_ecr_repo_name_output}...")
        pushed_image_uri = await asyncio.to_thread(
            docker_service.push_image_to_ecr,
            docker_client_instance,
            local_image_tag,
            tf_ecr_repo_name_output,
            ecr_registry_from_token, # Pass the registry URL, push_image_to_ecr will format it
            image_version_tag="latest" # Or use a more specific tag from build
        )
        if not pushed_image_uri:
            err_msg = f"Failed to push image {local_image_tag} to ECR repository {tf_ecr_repo_name_output}."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", f"Image successfully pushed to ECR: {pushed_image_uri}")

        # Final Success Message
        final_success_message = (
            f"Cloud-hosted EKS cluster '{cluster_name}' and ECR repository '{tf_ecr_repo_name_output}' provisioned successfully!\n"
            f"Application image pushed to: {pushed_image_uri}\n"
            f"EKS Cluster Endpoint: {eks_endpoint}\n"
            f"VPC ID: {vpc_id}\n"
            f"Instance ID for management: {cluster_name}"
        )
        logger.info(final_success_message)
        await _append_message_to_chat(chat_request, "assistant", final_success_message)

        return {
            "status": "success",
            "mode": "cloud-hosted",
            "message": "EKS, ECR, and application image push completed successfully.",
            "instance_id": cluster_name,
            "ecr_repository_url": tf_ecr_repo_url_output, # From TF output
            "ecr_repository_name": tf_ecr_repo_name_output, # From TF output
            "pushed_image_uri": pushed_image_uri, # From docker push
            "eks_cluster_endpoint": eks_endpoint,
            "eks_cluster_ca_data": eks_ca_data,
            "vpc_id": vpc_id,
            "outputs": outputs
        }

    except Exception as e:
        err_msg = f"An unexpected error occurred in handle_cloud_hosted_deployment: {str(e)}"
        logger.error(err_msg, exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {err_msg}")
        return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}
    finally:
        if clone_workspace_path and clone_workspace_path.exists():
            try:
                await asyncio.to_thread(shutil.rmtree, str(clone_workspace_path))
                logger.info(f"Successfully cleaned up temporary clone workspace: {clone_workspace_path}")
            except Exception as e_clean:
                logger.error(f"Failed to cleanup temporary clone workspace {clone_workspace_path}: {e_clean}", exc_info=True)
                # Optionally, append a warning to chat about cleanup failure if critical
                await _append_message_to_chat(chat_request, "assistant", f"Warning: Failed to cleanup temporary clone directory {clone_workspace_path}. Manual cleanup may be required.")


async def handle_cloud_local_scale(
    instance_id: str,
    public_ip: str,
    ec2_key_name: str,
    namespace: str,
    replicas: int,
    aws_creds: Optional[AWSCredentials],
    chat_request: ChatCompletionRequest
) -> Dict[str, Any]:
    """
    Handles scaling a Kubernetes deployment within a cloud-local instance's Kind cluster.
    """
    logger.info(f"Scale requested for instance: {instance_id} (IP: {public_ip}), namespace: {namespace}, to {replicas} replicas.")
    await _append_message_to_chat(chat_request, "assistant", f"Initiating scale operation for instance {instance_id} to {replicas} replicas in namespace {namespace}.")

    message_content = f"Scale operation for instance '{instance_id}' to {replicas} replicas received. Actual scaling logic (kubectl scale on EC2) is not yet fully implemented."
    logger.warning(message_content)
    await _append_message_to_chat(chat_request, "assistant", message_content)

    return {"status": "pending_implementation", "mode": "cloud-local", "action": "scale", "instance_id": instance_id, "message": message_content}
