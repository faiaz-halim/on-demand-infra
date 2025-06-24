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
from app.services import docker_service
from app.services import git_service
from app.services import k8s_service # Added for kubeconfig and Helm
import docker
import boto3

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
    logger.info(f"Local deployment requested for repo '{repo_url}' in namespace '{namespace}'.")
    message_content = f"Local deployment process started for {repo_url} in namespace {namespace}. Preparing environment..."
    await _append_message_to_chat(chat_request, "assistant", message_content)

    # Derive app name from repo URL
    repo_name = repo_url.split('/')[-1].replace('.git', '').replace('_', '-').lower()
    app_name = repo_name

    # 1. Check if Kind cluster exists, create if not
    kind_cluster_exists = await asyncio.to_thread(k8s_service.kind_cluster_exists, settings.KIND_CLUSTER_NAME)
    if not kind_cluster_exists:
        await _append_message_to_chat(chat_request, "assistant", f"Creating Kind cluster '{settings.KIND_CLUSTER_NAME}'...")
        create_ok, create_out = await asyncio.to_thread(k8s_service.create_kind_cluster, settings.KIND_CLUSTER_NAME, settings.KIND_CALICO_MANIFEST_URL)
        if not create_ok:
            err_msg = f"Failed to create Kind cluster: {create_out}"
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "local", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", f"Kind cluster '{settings.KIND_CLUSTER_NAME}' created successfully.")
    else:
        await _append_message_to_chat(chat_request, "assistant", f"Using existing Kind cluster '{settings.KIND_CLUSTER_NAME}'.")

        # 2. Clone the repository
        await _append_message_to_chat(chat_request, "assistant", f"Cloning repository {repo_url}...")
        clone_path = pathlib.Path(tempfile.mkdtemp(prefix="app_local_"))
        try:
            repo_dir = await asyncio.to_thread(git_service.clone_repository, repo_url, clone_path)
        except git_service.GitCloneError as e:
            err_msg = f"Failed to clone repository: {str(e)}"
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "local", "message": err_msg}

    # 3. Build Docker image
    image_tag = f"{app_name}:latest"
    await _append_message_to_chat(chat_request, "assistant", f"Building Docker image {image_tag}...")
    build_result = await asyncio.to_thread(docker_service.build_docker_image_locally, str(repo_dir), image_tag)
    if not build_result.get("success"):
        err_msg = f"Failed to build Docker image: {build_result.get('error', 'Unknown error')}"
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        shutil.rmtree(str(clone_path))
        return {"status": "error", "mode": "local", "message": err_msg}

    # 4. Load image into Kind cluster
    await _append_message_to_chat(chat_request, "assistant", f"Loading image {image_tag} into Kind cluster...")
    load_ok, load_out = await asyncio.to_thread(k8s_service.load_image_to_kind, settings.KIND_CLUSTER_NAME, image_tag)
    if not load_ok:
        err_msg = f"Failed to load image to Kind cluster: {load_out}"
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        shutil.rmtree(str(clone_path))
        return {"status": "error", "mode": "local", "message": err_msg}

    # 5. Deploy to Kind cluster
    await _append_message_to_chat(chat_request, "assistant", f"Deploying application to Kind cluster in namespace '{namespace}'...")
    # Create namespace if not exists
    ns_ok, ns_out = await asyncio.to_thread(k8s_service.create_namespace, namespace, settings.KIND_CLUSTER_NAME)
    if not ns_ok:
        err_msg = f"Failed to create namespace {namespace}: {ns_out}"
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        shutil.rmtree(str(clone_path))
        return {"status": "error", "mode": "local", "message": err_msg}

    # Generate manifests
    container_port = 80  # default port, can be customized
    deployment_yaml = manifest_service.generate_deployment_manifest(image_tag, app_name, 1, [container_port], namespace)
    service_yaml = manifest_service.generate_service_manifest(app_name, "NodePort", [{"port": 80, "targetPort": container_port}], namespace)

    # Apply manifests
    apply_ok, apply_out = await asyncio.to_thread(k8s_service.apply_manifests, None, namespace, deployment_yaml + "\n---\n" + service_yaml)
    if not apply_ok:
        err_msg = f"Failed to apply manifests: {apply_out}"
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        shutil.rmtree(str(clone_path))
        return {"status": "error", "mode": "local", "message": err_msg}

    # Get service details
    service_info = await asyncio.to_thread(k8s_service.get_service_info, app_name, namespace, settings.KIND_CLUSTER_NAME)
    if not service_info:
        err_msg = f"Failed to get service details for {app_name}"
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        shutil.rmtree(str(clone_path))
        return {"status": "error", "mode": "local", "message": err_msg}

    # Cleanup
    shutil.rmtree(str(clone_path))

    success_msg = f"Application deployed successfully to Kind cluster. Service details: {service_info}"
    await _append_message_to_chat(chat_request, "assistant", success_msg)
    return {"status": "success", "mode": "local", "message": success_msg, "service_info": service_info}


async def handle_cloud_local_deployment(
    repo_url: str,
    namespace: str,
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest
):
    # ... (implementation as before, from previous steps) ...
    repo_name_part = repo_url.split('/')[-1].replace('.git', '').replace('.', '-')
    unique_id = uuid.uuid4().hex[:6]
    instance_name_tag = chat_request.instance_id or f"appcl-{repo_name_part}-{unique_id}"
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
            err_msg = f"SSH private key '{ec2_key_name_to_use}' not found at {private_key_full_path}. Please ensure the key exists on the server at the configured EC2_PRIVATE_KEY_BASE_PATH or was provided correctly."
            await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        remote_repo_path = f"{settings.EC2_DEFAULT_REPO_PATH}/{app_name}"
        clone_command = f"sudo rm -rf {remote_repo_path} && git clone --depth 1 {repo_url} {remote_repo_path}"
        await _append_message_to_chat(chat_request, "assistant", f"Cloning repository {repo_url} on EC2...")
        await asyncio.sleep(15) # Allowing some time for network/instance to be fully ready
        _, clone_stderr, clone_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), clone_command)
        if clone_exit_code != 0:
            err_msg = f"Failed to clone repository '{repo_url}' on EC2 instance {public_ip}. Please check the repository URL and ensure the EC2 instance has network access to it. SSH command stderr: {clone_stderr or 'No stderr'}"
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", "Repository cloned.")

        built_image_tag = f"{app_name}:latest"
        docker_build_command = f"cd {remote_repo_path} && sudo docker build -t {built_image_tag} ."
        await _append_message_to_chat(chat_request, "assistant", f"Building Docker image {built_image_tag} on EC2...")
        _, build_stderr, build_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), docker_build_command)
        if build_exit_code != 0:
            err_msg = f"Failed to build Docker image on EC2 instance {public_ip} from repo {repo_url}. Review build logs for details. SSH command stderr: {build_stderr or 'No stderr'}"
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", "Docker image built.")

        load_image_command = f"sudo kind load docker-image {built_image_tag} --name {settings.KIND_CLUSTER_NAME}"
        await _append_message_to_chat(chat_request, "assistant", f"Loading image into Kind...")
        _, load_stderr, load_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), load_image_command)
        if load_exit_code != 0:
            err_msg = f"Failed to load Docker image '{built_image_tag}' to Kind cluster on EC2 instance {public_ip}. SSH command stderr: {load_stderr or 'No stderr'}"
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", "Image loaded to Kind.")

        container_port = settings.EC2_DEFAULT_APP_PORTS[0]['port'] if settings.EC2_DEFAULT_APP_PORTS else 80
        deployment_yaml = manifest_service.generate_deployment_manifest(image_name=built_image_tag, app_name=app_name, replicas=1, ports=[container_port], namespace=namespace)
        service_yaml = manifest_service.generate_service_manifest(app_name=app_name, service_type="NodePort", ports_mapping=[{'port': 80, 'targetPort': container_port, 'nodePort': node_port_num}], namespace=namespace)
        if not deployment_yaml or not service_yaml: err_msg = "Failed to generate K8s manifests." ; await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}"); return {"status": "error", "message": err_msg}

        local_manifest_temp_dir_path = pathlib.Path(tempfile.mkdtemp(prefix="app_manifests_local_"))
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
        _, apply_m_stderr, apply_m_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), apply_command)
        if apply_m_exit_code != 0:
            err_msg = f"Failed to apply Kubernetes manifests to Kind cluster on EC2 instance {public_ip}. SSH command stderr: {apply_m_stderr or 'No stderr'}"
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        cleanup_remote_cmd = f"rm -rf {remote_manifest_dir}"
        await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), cleanup_remote_cmd)

        app_url = f"http://{public_ip}:{node_port_num}"
        final_success_message = f"Application '{app_name}' deployed to Kind on EC2 instance '{instance_name_tag}' (IP: {public_ip}) in namespace '{namespace}'. Instance ID for management: {instance_name_tag}. Application may be accessible at: {app_url}"
        await _append_message_to_chat(chat_request, "assistant", final_success_message)

        return {"status": "success", "mode": "cloud-local", "message": final_success_message, "outputs": outputs,
                "instance_id": instance_name_tag,
                "ec2_public_ip": public_ip, "built_image_tag": built_image_tag, "app_url": app_url}

    except Exception as e:
        err_msg = f"An unexpected error in handle_cloud_local_deployment for instance '{instance_name_tag}': {str(e)}"
        logger.error(f"An unexpected error in handle_cloud_local_deployment for instance '{instance_name_tag}': {str(e)}", exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {err_msg}")
        return {"status": "error", "mode": "cloud-local", "message": err_msg, "instance_id": instance_name_tag}


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
        err_msg = f"Workspace for instance ID '{instance_id}' not found at expected location: {workspace_dir}. Cannot perform decommission."
        logger.error(err_msg)
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        return {"status": "error", "message": err_msg, "instance_id": instance_id}

    logger.info(f"Using Terraform workspace for decommission: {workspace_dir}")

    aws_env_vars = {
        "AWS_ACCESS_KEY_ID": aws_creds.aws_access_key_id.get_secret_value(),
        "AWS_SECRET_ACCESS_KEY": aws_creds.aws_secret_access_key.get_secret_value(),
        "AWS_DEFAULT_REGION": aws_creds.aws_region,
    }
    logger.info(f"AWS environment variables prepared for decommission. Region: {aws_creds.aws_region}.")

    await _append_message_to_chat(chat_request, "assistant", f"Initializing Terraform for instance '{instance_id}' at {workspace_dir}...")
    init_ok, init_out, init_err = await asyncio.to_thread(
        terraform_service.run_terraform_init, str(workspace_dir), aws_env_vars
    )
    logger.info(f"Terraform init stdout for decommission of '{instance_id}':\n{init_out}")
    if init_err:
        logger.error(f"Terraform init stderr for decommission of '{instance_id}':\n{init_err}")

    if not init_ok:
        err_msg = f"Terraform init failed for instance '{instance_id}': {init_err or init_out}"
        logger.error(err_msg)
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        return {"status": "error", "message": err_msg, "instance_id": instance_id}

    await _append_message_to_chat(chat_request, "assistant", f"Starting Terraform destroy for instance '{instance_id}'. This may take a few minutes...")
    destroy_ok, destroy_out, destroy_err = await asyncio.to_thread(
        terraform_service.run_terraform_destroy, str(workspace_dir), aws_env_vars
    )
    logger.info(f"Terraform destroy stdout for '{instance_id}':\n{destroy_out}")
    if destroy_err:
        logger.error(f"Terraform destroy stderr for '{instance_id}':\n{destroy_err}")

    if destroy_ok:
        success_msg = f"Terraform destroy successful for instance '{instance_id}'."
        logger.info(success_msg)
        await _append_message_to_chat(chat_request, "assistant", success_msg)
        cleanup_status_msg = ""
        try:
            await asyncio.to_thread(shutil.rmtree, str(workspace_dir))
            logger.info(f"Successfully removed workspace directory: {workspace_dir}")
            cleanup_status_msg = "Workspace directory successfully removed."
        except Exception as e:
            logger.error(f"Failed to remove workspace directory {workspace_dir}: {str(e)}", exc_info=True)
            cleanup_status_msg = f"Workspace directory removal failed: {str(e)}. Manual cleanup may be required."

        final_message = f"Instance '{instance_id}' decommissioned. {cleanup_status_msg}"
        await _append_message_to_chat(chat_request, "assistant", final_message)
        return {"status": "success", "message": final_message, "instance_id": instance_id}
    else:
        err_msg = f"Terraform destroy failed for instance '{instance_id}'. Stdout: {destroy_out} Stderr: {destroy_err}"
        logger.error(err_msg)
        await _append_message_to_chat(chat_request, "assistant", f"Error: Terraform destroy failed. Details: {destroy_err or destroy_out}")
        return {"status": "error", "message": err_msg, "instance_id": instance_id}


async def handle_cloud_local_redeploy(
    instance_id: str,
    public_ip: str,
    ec2_key_name: str,
    repo_url: str,
    namespace: str,
    aws_creds: Optional[AWSCredentials],
    chat_request: ChatCompletionRequest
) -> Dict[str, Any]:
    logger.info(f"Redeploy requested for cloud-local instance: {instance_id} (IP: {public_ip}), new repo: {repo_url}, K8s namespace: {namespace}")
    await _append_message_to_chat(chat_request, "assistant", f"Initiating redeploy for instance {instance_id} (IP: {public_ip}) with new repository {repo_url} in namespace {namespace}.")

    try:
        app_name = repo_url.split('/')[-1].replace('.git', '').lower().replace('_', '-')
        logger.info(f"Derived app_name for redeploy: {app_name}")

        if not settings.EC2_PRIVATE_KEY_BASE_PATH:
            raise ValueError("Server configuration error: EC2_PRIVATE_KEY_BASE_PATH is not set.")

        private_key_full_path = pathlib.Path(settings.EC2_PRIVATE_KEY_BASE_PATH) / ec2_key_name
        if not private_key_full_path.exists():
            raise FileNotFoundError(f"SSH private key '{ec2_key_name}' not found at configured base path.")

        # Use a unique temporary path for cloning the new repo version on the remote EC2 instance
        remote_repo_clone_path = f"/tmp/{app_name}_redeploy_src_{uuid.uuid4().hex[:6]}"

        # 1. Git Clone (Remote)
        await _append_message_to_chat(chat_request, "assistant", f"Cloning new version of repository {repo_url} on EC2 instance {public_ip} at {remote_repo_clone_path}...")
        clone_command = f"rm -rf {remote_repo_clone_path} && git clone --depth 1 {repo_url} {remote_repo_clone_path}"
        clone_stdout, clone_stderr, clone_exit_code = await asyncio.to_thread(
            ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), clone_command
        )
        if clone_exit_code != 0:
            err_msg = f"Failed to clone new repository version on EC2: {clone_stderr or clone_stdout}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "message": err_msg, "instance_id": instance_id}
        await _append_message_to_chat(chat_request, "assistant", "New repository version cloned successfully on EC2.")

        # 2. Docker Build (Remote)
        new_image_tag = f"{app_name}:redeploy-{uuid.uuid4().hex[:8]}"
        await _append_message_to_chat(chat_request, "assistant", f"Building Docker image {new_image_tag} on EC2 from {remote_repo_clone_path}...")
        docker_build_command = f"cd {remote_repo_clone_path} && sudo docker build -t {new_image_tag} ."
        build_stdout, build_stderr, build_exit_code = await asyncio.to_thread(
            ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), docker_build_command
        )
        if build_exit_code != 0:
            err_msg = f"Failed to build Docker image on EC2: {build_stderr or build_stdout}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "message": err_msg, "instance_id": instance_id}
        await _append_message_to_chat(chat_request, "assistant", f"Docker image {new_image_tag} built successfully on EC2.")

        # 3. Load Image to Kind (Remote)
        await _append_message_to_chat(chat_request, "assistant", f"Loading image {new_image_tag} into Kind cluster '{settings.KIND_CLUSTER_NAME}' on EC2...")
        load_image_command = f"sudo kind load docker-image {new_image_tag} --name {settings.KIND_CLUSTER_NAME}"
        load_stdout, load_stderr, load_exit_code = await asyncio.to_thread(
            ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), load_image_command
        )
        if load_exit_code != 0:
            err_msg = f"Failed to load image to Kind cluster: {load_stderr or load_stdout}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "message": err_msg, "instance_id": instance_id}
        await _append_message_to_chat(chat_request, "assistant", "Image loaded into Kind cluster successfully.")

        # 4. Update Kubernetes Deployment (Remote)
        # Assuming deployment name and container name match app_name
        deployment_name = app_name
        container_name_to_update = app_name
        await _append_message_to_chat(chat_request, "assistant", f"Updating Kubernetes deployment '{deployment_name}' in namespace '{namespace}' to use new image {new_image_tag}...")
        update_command = f"sudo kubectl set image deployment/{deployment_name} {container_name_to_update}={new_image_tag} --namespace {namespace}"
        update_stdout, update_stderr, update_exit_code = await asyncio.to_thread(
            ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), update_command
        )
        if update_exit_code != 0:
            err_msg = f"Failed to update Kubernetes deployment: {update_stderr or update_stdout}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "message": err_msg, "instance_id": instance_id}
        await _append_message_to_chat(chat_request, "assistant", "Kubernetes deployment updated successfully. Rollout may take a few moments.")

        # 5. Cleanup (Remote)
        await _append_message_to_chat(chat_request, "assistant", f"Cleaning up temporary source code directory {remote_repo_clone_path} on EC2...")
        cleanup_command = f"rm -rf {remote_repo_clone_path}"
        # Execute cleanup, log errors but don't fail the entire operation if only cleanup fails
        clean_stdout, clean_stderr, clean_exit_code = await asyncio.to_thread(
            ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), cleanup_command
        )
        if clean_exit_code != 0:
            logger.warning(f"Failed to cleanup remote directory {remote_repo_clone_path} on {public_ip}. Stderr: {clean_stderr or clean_stdout}")
            await _append_message_to_chat(chat_request, "assistant", f"Warning: Failed to cleanup temporary directory on EC2: {remote_repo_clone_path}. Manual cleanup may be needed.")
        else:
            await _append_message_to_chat(chat_request, "assistant", "Remote cleanup successful.")

        final_success_message = f"Redeployment of application '{app_name}' on instance '{instance_id}' (IP: {public_ip}) in namespace '{namespace}' with new image '{new_image_tag}' completed successfully."
        logger.info(final_success_message)
        await _append_message_to_chat(chat_request, "assistant", final_success_message)
        return {
            "status": "success",
            "message": final_success_message,
            "instance_id": instance_id,
            "new_image_tag": new_image_tag
        }

    except FileNotFoundError as fnf_err:
        logger.error(f"Configuration error during redeploy for {instance_id}: {str(fnf_err)}", exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {str(fnf_err)}")
        return {"status": "error", "message": str(fnf_err), "instance_id": instance_id}
    except ValueError as val_err:
        logger.error(f"Value error during redeploy for {instance_id}: {str(val_err)}", exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {str(val_err)}")
        return {"status": "error", "message": str(val_err), "instance_id": instance_id}
    except Exception as e:
        err_msg = f"An unexpected error occurred during cloud-local redeploy for instance '{instance_id}': {str(e)}"
        logger.error(err_msg, exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {err_msg}")
        return {"status": "error", "message": err_msg, "instance_id": instance_id}


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
        clone_workspace_path = pathlib.Path(tempfile.mkdtemp(prefix="app_clone_ch_"))
        logger.info(f"Created temporary clone workspace: {clone_workspace_path}")
        await _append_message_to_chat(chat_request, "assistant", f"Cloning repository {repo_url} locally for image build...")

        cloned_repo_details = await asyncio.to_thread(git_service.clone_repository, repo_url, clone_workspace_path) # type: ignore
        # The above type: ignore is because clone_repository now returns a Path, not a dict.
        # This part of the code was based on an older version of git_service.clone_repository
        # For now, let's assume it might return a dict with 'success' and 'error' keys if it failed in a specific way,
        # or raises an exception (like GitCloneError) which would be caught by the main try/except.
        # A more robust way would be to update clone_repository to consistently raise exceptions on failure.
        # For this subtask, focusing on error message text. If clone_repository raises an exception, it's caught below.
        # If it returns a dict (older behavior), this check handles it.
        if isinstance(cloned_repo_details, dict) and not cloned_repo_details.get("success"): # Check for old dict error format
            err_msg = f"Failed to clone repository {repo_url} locally on the server: {cloned_repo_details.get('error', 'Unknown error')}. Please check the repository URL and network access."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        # If clone_repository returned a Path object (current behavior for success)
        actual_cloned_path: pathlib.Path
        if isinstance(cloned_repo_details, pathlib.Path):
            actual_cloned_path = cloned_repo_details
        elif isinstance(cloned_repo_details, dict) and cloned_repo_details.get("success") and cloned_repo_details.get("path"): # Older dict success format
             actual_cloned_path = pathlib.Path(cloned_repo_details["path"])
        else: # Fallback if structure is unexpected or if clone_repository path is directly in clone_workspace_path
            actual_cloned_path = clone_workspace_path / repo_name_from_url
            if not actual_cloned_path.exists() or not actual_cloned_path.is_dir(): # Check if it's directly in clone_workspace_path
                actual_cloned_path = clone_workspace_path

        if not actual_cloned_path.exists() or not actual_cloned_path.is_dir(): # Final check
            err_msg = f"Cloned repository directory structure unexpected or not found at {actual_cloned_path} or {clone_workspace_path}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
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
        local_image_tag = f"{repo_name_from_url.lower()}-app:{uuid.uuid4().hex[:8]}"
        await _append_message_to_chat(chat_request, "assistant", f"Building Docker image {local_image_tag} locally...")
        build_result = await asyncio.to_thread(docker_service.build_docker_image_locally, actual_cloned_path, local_image_tag)
        build_logs = build_result.get('logs', '')
        logger.info(f"Docker build logs for {local_image_tag}:\n{build_logs}")

        if not build_result.get("success"):
            log_snippet = build_logs[-500:] if build_logs else "No logs available."
            err_msg = f"Failed to build Docker image {local_image_tag} locally on the server: {build_result.get('error', 'Unknown build error')}. Log snippet: {log_snippet}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
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
            err_msg = "Failed to get AWS ECR login credentials. Please check the provided AWS credentials and server permissions."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        ecr_username, ecr_password, ecr_registry_from_token = login_details

        try:
            docker_client_instance = docker.from_env()
        except Exception as e:
            err_msg = f"Failed to initialize Docker client for ECR login: {str(e)}"
            logger.error(err_msg, exc_info=True) # exc_info=True added
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        login_ok = await asyncio.to_thread(
            docker_service.login_to_ecr,
            docker_client_instance,
            ecr_registry_from_token,
            ecr_username,
            ecr_password
        )
        if not login_ok:
            err_msg = "AWS ECR login failed. Please check the provided AWS credentials and server permissions for ECR access."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", "Successfully authenticated with ECR.")

        # 8. Push Image to ECR
        await _append_message_to_chat(chat_request, "assistant", f"Pushing image {local_image_tag} to ECR repository {tf_ecr_repo_name_output}...")
        pushed_image_uri = await asyncio.to_thread(
            docker_service.push_image_to_ecr,
            docker_client_instance,
            local_image_tag,
            tf_ecr_repo_name_output,
            ecr_registry_from_token,
            image_version_tag="latest"
        )
        if not pushed_image_uri:
            err_msg = f"Failed to push image {local_image_tag} to ECR repository {tf_ecr_repo_name_output}. Ensure the repository exists and permissions are correct."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", f"Image successfully pushed to ECR: {pushed_image_uri}")

        # 9. Generate Kubeconfig for EKS cluster
        await _append_message_to_chat(chat_request, "assistant", "Generating Kubeconfig for EKS cluster...")
        if not eks_ca_data:
            err_msg = "EKS CA data is missing, cannot generate kubeconfig." # No change needed, already specific
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        kubeconfig_file_path = await asyncio.to_thread(
            k8s_service.generate_eks_kubeconfig_file,
            cluster_name=cluster_name,
            endpoint_url=eks_endpoint,
            ca_data=eks_ca_data,
            aws_region=aws_creds.aws_region,
            user_arn=settings.EKS_DEFAULT_USER_ARN,
            output_dir=str(workspace_dir_path)
        )
        if not kubeconfig_file_path:
            err_msg = f"Critical Error: Failed to generate Kubeconfig for EKS cluster '{cluster_name}'. This might indicate an issue with EKS cluster provisioning or output retrieval."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", f"Kubeconfig saved to workspace. Installing Nginx Ingress Controller via Helm...")

        # 10. Install Nginx Ingress Controller using Helm
        nginx_install_ok = await asyncio.to_thread(
            k8s_service.install_nginx_ingress_helm,
            kubeconfig_path=kubeconfig_file_path,
            namespace="ingress-nginx",
            helm_chart_version=settings.NGINX_HELM_CHART_VERSION
        )
        if not nginx_install_ok:
            err_msg = f"Failed to install Nginx Ingress Controller via Helm on EKS cluster '{cluster_name}'. Check Helm and Kubernetes logs on the cluster if possible."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", "Nginx Ingress Controller installation successful.")

        # 11. Get Load Balancer details for Nginx Ingress
        await _append_message_to_chat(chat_request, "assistant", "Fetching Load Balancer details for Nginx Ingress...")
        nlb_details = await asyncio.to_thread(
            k8s_service.get_load_balancer_details,
            kubeconfig_path=kubeconfig_file_path,
            service_name=settings.NGINX_INGRESS_SERVICE_NAME,
            namespace=settings.NGINX_INGRESS_NAMESPACE,
            timeout_seconds=settings.LOAD_BALANCER_DETAILS_TIMEOUT_SECONDS
        )
        if not nlb_details or not nlb_details[0]:
            err_msg = f"Failed to get Nginx Load Balancer DNS details from EKS cluster '{cluster_name}'. The NLB might not have provisioned correctly or is taking too long."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        nlb_dns_name, _ = nlb_details

        nlb_canonical_hosted_zone_id = None
        try:
            elbv2_client = boto3.client(
                'elbv2',
                aws_access_key_id=aws_creds.aws_access_key_id.get_secret_value(),
                aws_secret_access_key=aws_creds.aws_secret_access_key.get_secret_value(),
                region_name=aws_creds.aws_region
            )
            all_lbs_response = elbv2_client.describe_load_balancers()
            found_lb = None
            for lb in all_lbs_response.get('LoadBalancers', []):
                if lb.get('DNSName') == nlb_dns_name:
                    found_lb = lb
                    break

            if found_lb:
                nlb_canonical_hosted_zone_id = found_lb.get('CanonicalHostedZoneId')
                if nlb_canonical_hosted_zone_id:
                    logger.info(f"Successfully fetched CanonicalHostedZoneId '{nlb_canonical_hosted_zone_id}' for NLB '{nlb_dns_name}'.")
                    await _append_message_to_chat(chat_request, "assistant", f"Successfully fetched NLB details for Route53 setup.")
                else:
                    # This specific error message will be part of the ValueError raised.
                    logger.error(f"Found NLB '{nlb_dns_name}' but it does not have a CanonicalHostedZoneId.")
                    await _append_message_to_chat(chat_request, "assistant", f"Error: NLB '{nlb_dns_name}' found, but CanonicalHostedZoneId is missing. Cannot proceed with Route53 alias record.")
                    raise ValueError(f"Critical: NLB '{nlb_dns_name}' was found, but its CanonicalHostedZoneId is missing. Cannot configure Route53 alias record.")
            else:
                 # This specific error message will be part of the ValueError raised.
                logger.error(f"Could not find NLB with DNSName '{nlb_dns_name}' via AWS API to fetch its CanonicalHostedZoneId.")
                await _append_message_to_chat(chat_request, "assistant", f"Error: Could not find NLB with DNSName '{nlb_dns_name}' using AWS API. Cannot determine CanonicalHostedZoneId for Route53.")
                raise ValueError(f"Critical: Could not find an NLB with DNSName '{nlb_dns_name}' via AWS API. Cannot configure Route53 alias record.")

        except Exception as e_elb: # Catch Boto3 client errors or the ValueErrors raised above
            # If the error is one of our custom ValueErrors for HZID/NLB not found, its message is already specific.
            # Otherwise, it's an unexpected Boto3/AWS API error.
            specific_error_message = str(e_elb) if isinstance(e_elb, ValueError) else f"Error fetching NLB details from AWS API: {str(e_elb)}"
            logger.error(f"Failed to describe load balancers or get CanonicalHostedZoneId for '{nlb_dns_name}': {specific_error_message}", exc_info=True)
            await _append_message_to_chat(chat_request, "assistant", specific_error_message) # Use the specific error message
            raise e_elb # Re-raise to be caught by the main try/except which will then use this message.

        await _append_message_to_chat(chat_request, "assistant", f"Nginx Load Balancer DNS: {nlb_dns_name}, Canonical HZID: {nlb_canonical_hosted_zone_id}. Proceeding with domain and certificate setup...")

        # 12. Terraform for Route53/ACM (if base_hosted_zone_id and app_subdomain_label are provided)
        app_full_domain_name = None
        acm_certificate_arn = None # Will be set by TF if created
        app_url_https = None # Will be set by TF if created

        if chat_request.base_hosted_zone_id and chat_request.app_subdomain_label:
            if not settings.DEFAULT_DOMAIN_NAME_FOR_APPS:
                err_msg = "Configuration Error: DEFAULT_DOMAIN_NAME_FOR_APPS is not set, cannot construct full domain name."
                logger.error(err_msg)
                await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
                return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

            app_full_domain_name = f"{chat_request.app_subdomain_label}.{settings.DEFAULT_DOMAIN_NAME_FOR_APPS}"
            await _append_message_to_chat(chat_request, "assistant", f"Setting up domain '{app_full_domain_name}' and SSL certificate...")

            route53_tf_context = {
                "aws_region": aws_creds.aws_region,
                "base_hosted_zone_id": chat_request.base_hosted_zone_id,
                "app_full_domain_name": app_full_domain_name,
                "nlb_dns_name": nlb_dns_name,
                "nlb_hosted_zone_id": nlb_canonical_hosted_zone_id
            }
            route53_tf_file = await asyncio.to_thread(
                terraform_service.generate_route53_acm_tf_config,
                route53_tf_context,
                str(workspace_dir_path),
                filename_override=settings.ROUTE53_ACM_TF_FILENAME
            )
            if not route53_tf_file:
                err_msg = "Failed to generate Terraform configuration for Route53/ACM."
                logger.error(err_msg)
                await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
                return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

            await _append_message_to_chat(chat_request, "assistant", "Applying Terraform for Route53 and ACM certificate (this may take a few minutes for DNS propagation and validation)...")
            # This apply will add to the existing state, effectively applying only the new resources.
            apply_domain_ok, domain_outputs, domain_apply_out, domain_apply_err = await asyncio.to_thread(
                terraform_service.run_terraform_apply, str(workspace_dir_path), aws_env_vars
            )
            logger.info(f"Terraform apply (Route53/ACM) stdout:\n{domain_apply_out}")
            if domain_apply_err: logger.error(f"Terraform apply (Route53/ACM) stderr:\n{domain_apply_err}")

            if not apply_domain_ok:
                err_msg = f"Terraform apply failed for Route53/ACM: {domain_apply_err or domain_apply_out}"
                logger.error(err_msg)
                await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}.")
                return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

            acm_certificate_arn = domain_outputs.get("acm_certificate_arn", {}).get("value")
            app_url_https = domain_outputs.get("app_url_https", {}).get("value")

            if not acm_certificate_arn or not app_url_https:
                err_msg = "Failed to get ACM certificate ARN or HTTPS URL from Terraform outputs after Route53/ACM setup."
                logger.error(f"{err_msg} Outputs: {domain_outputs}")
                await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
                return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

            await _append_message_to_chat(chat_request, "assistant", f"Domain '{app_full_domain_name}' and SSL certificate (ARN: {acm_certificate_arn}) configured.")
        else:
            logger.info("Skipping Route53/ACM setup as base_hosted_zone_id or app_subdomain_label not provided.")
            await _append_message_to_chat(chat_request, "assistant", "Skipping custom domain and SSL certificate setup as required details were not provided.")


        # 13. Generate Kubernetes Manifests for EKS (using the application's target namespace)
        await _append_message_to_chat(chat_request, "assistant", f"Generating Kubernetes manifests for EKS deployment using image: {pushed_image_uri}...")
        app_name = repo_name_from_url.lower().replace('_', '-') # Consistent app name
        target_namespace_to_use = namespace

        # Determine container_port (this logic might need refinement based on actual app introspection)
        try:
            default_app_ports_list = json.loads(settings.EC2_DEFAULT_APP_PORTS_JSON) # Reusing this setting for now
            container_port = int(default_app_ports_list[0]['port']) if default_app_ports_list and isinstance(default_app_ports_list[0].get('port'), (int,str)) else 80
        except (json.JSONDecodeError, IndexError, TypeError, ValueError) as e:
            logger.warning(f"Could not parse EC2_DEFAULT_APP_PORTS_JSON ('{settings.EC2_DEFAULT_APP_PORTS_JSON}') or it was empty/invalid. Defaulting container_port to 80. Error: {e}")
            container_port = 80

        deployment_yaml_content = manifest_service.generate_deployment_manifest(
            image_name=pushed_image_uri,
            app_name=app_name,
            replicas=1,
            ports=[container_port],
            namespace=target_namespace_to_use
        )
        service_yaml_content = manifest_service.generate_service_manifest(
            app_name=app_name,
            service_type="ClusterIP",
            ports_mapping=[{'port': 80, 'targetPort': container_port, 'protocol': 'TCP'}],
            namespace=target_namespace_to_use
        )

        if deployment_yaml_content is None or service_yaml_content is None:
            err_msg = "Failed to generate Kubernetes Deployment/Service manifests for EKS."
            # ... (error handling as before)
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        # Save Deployment and Service manifests
        try:
            deployment_file_path = workspace_dir_path / f"{app_name}_deployment.yaml"
            service_file_path = workspace_dir_path / f"{app_name}_service.yaml"
            with open(deployment_file_path, "w") as f: f.write(deployment_yaml_content)
            with open(service_file_path, "w") as f: f.write(service_yaml_content)
            logger.info(f"Saved K8s Deployment to: {deployment_file_path}, Service to: {service_file_path}")
            await _append_message_to_chat(chat_request, "assistant", "App Deployment and Service manifests saved.")
        except IOError as e:
            err_msg = f"Failed to save K8s Deployment/Service manifests: {e}"
            # ... (error handling as before)
            logger.error(err_msg, exc_info=True)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        # 14. Generate Ingress Manifest (if domain name is available)
        if app_full_domain_name:
            await _append_message_to_chat(chat_request, "assistant", f"Generating Ingress manifest for host: {app_full_domain_name}...")
            ingress_context = {
                "namespace": target_namespace_to_use,
                "ingress_name": f"{app_name}-ingress",
                "host_name": app_full_domain_name,
                "service_name": app_name, # K8s service name for the app
                "service_port": 80,       # Port on the K8s service (ClusterIP service listens on 80)
                "acm_certificate_arn": acm_certificate_arn, # From TF Route53/ACM step
                "ssl_redirect": settings.INGRESS_DEFAULT_SSL_REDIRECT,
                # Other optional settings like path_type, http_path can be added from settings or request
                "http_path": settings.INGRESS_DEFAULT_HTTP_PATH,
                "path_type": settings.INGRESS_DEFAULT_PATH_TYPE,
            }
            ingress_yaml_content = manifest_service.generate_ingress_manifest(ingress_context)
            if not ingress_yaml_content:
                err_msg = "Failed to generate Kubernetes Ingress manifest."
                logger.error(err_msg)
                await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
                return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

            try:
                ingress_file_path = workspace_dir_path / f"{app_name}_ingress.yaml"
                with open(ingress_file_path, "w") as f: f.write(ingress_yaml_content)
                logger.info(f"Saved K8s Ingress manifest to: {ingress_file_path}")
                await _append_message_to_chat(chat_request, "assistant", "K8s Ingress manifest generated and saved.")
            except IOError as e:
                err_msg = f"Failed to save K8s Ingress manifest: {e}"
                logger.error(err_msg, exc_info=True)
                await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
                return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}
        else:
            await _append_message_to_chat(chat_request, "assistant", "Skipping Ingress generation as no custom domain was configured.")

        # 15. Apply All K8s Manifests (App Deployment, Service, Ingress if generated)
        await _append_message_to_chat(chat_request, "assistant", "Applying application and Ingress manifests to EKS cluster...")
        # apply_manifests expects a directory. All our manifests (app deploy, service, ingress) are in workspace_dir_path.
        apply_k8s_ok = await asyncio.to_thread(
            k8s_service.apply_manifests,
            kubeconfig_path=kubeconfig_file_path,
            manifest_dir_or_file=str(workspace_dir_path), # Directory containing all YAMLs
            namespace=target_namespace_to_use
        )
        if not apply_k8s_ok:
            err_msg = f"Failed to apply K8s manifests for app '{app_name}' to namespace '{target_namespace_to_use}'."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-hosted", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", "Application K8s manifests applied successfully.")

        # Final Success Message
        final_url = app_url_https if app_url_https else f"http://{nlb_dns_name}" # Fallback to NLB DNS if no custom domain
        final_success_message = (
            f"Cloud-hosted EKS deployment for '{app_name}' completed!\n"
            f"Application URL: {final_url}\n"
            f"ECR Repository: {pushed_image_uri}\n"
            f"EKS Cluster Endpoint: {eks_endpoint}\n"
            f"Instance ID (Cluster Name) for management: {cluster_name}"
        )
        logger.info(final_success_message)
        await _append_message_to_chat(chat_request, "assistant", final_success_message)

        return {
            "status": "success",
            "mode": "cloud-hosted",
            "message": f"Deployment successful. App URL: {final_url}",
            "instance_id": cluster_name,
            "ecr_repository_url": tf_ecr_repo_url_output,
            "ecr_repository_name": tf_ecr_repo_name_output,
            "pushed_image_uri": pushed_image_uri,
            "eks_cluster_endpoint": eks_endpoint,
            "eks_cluster_ca_data": eks_ca_data,
            "vpc_id": vpc_id,
            "k8s_manifest_dir": str(workspace_dir_path),
            "app_url_https": app_url_https,
            "nlb_dns_name": nlb_dns_name,
            "acm_certificate_arn": acm_certificate_arn,
            "outputs": outputs
        }

    except Exception as e:
        # The error message `err_msg` here will be the one from the re-raised exception if it was a ValueError from NLB/HZID lookup,
        # or a generic one if it's another unexpected error.
        err_msg = f"An unexpected error in handle_cloud_hosted_deployment for cluster '{cluster_name}': {str(e)}"
        logger.error(f"An unexpected error in handle_cloud_hosted_deployment for cluster '{cluster_name}': {str(e)}", exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {str(e)}") # Send the original exception message to chat for more direct info
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
    logger.info(f"Cloud-local scale operation started for instance: {instance_id} (IP: {public_ip}), namespace: {namespace}, to {replicas} replicas.")
    await _append_message_to_chat(chat_request, "assistant", f"Initiating scale operation for instance '{instance_id}' to {replicas} replicas in namespace '{namespace}'.")

    try:
        # Derive app_name (deployment name) from github_repo_url in the chat_request
        if not chat_request.github_repo_url:
            err_msg = "GitHub repository URL is required to determine the deployment name for scaling, but it was not provided in the request."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
            return {"status": "error", "message": err_msg, "instance_id": instance_id}

        derived_app_name = chat_request.github_repo_url.split('/')[-1].replace('.git', '').lower().replace('_', '-')
        logger.info(f"Derived deployment name for scaling: {derived_app_name}")

        if not settings.EC2_PRIVATE_KEY_BASE_PATH:
            raise ValueError("Server configuration error: EC2_PRIVATE_KEY_BASE_PATH is not set.")

        private_key_full_path = pathlib.Path(settings.EC2_PRIVATE_KEY_BASE_PATH) / ec2_key_name
        if not private_key_full_path.exists():
            raise FileNotFoundError(f"SSH private key '{ec2_key_name}' not found at configured base path: {private_key_full_path}")

        # Execute Remote Kubectl Scale Command
        scale_command = f"sudo kubectl scale deployment/{derived_app_name} --replicas={replicas} --namespace {namespace}"
        await _append_message_to_chat(chat_request, "assistant", f"Executing scale command on EC2 instance {public_ip}: {scale_command}")

        scale_stdout, scale_stderr, scale_exit_code = await asyncio.to_thread(
            ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, str(private_key_full_path), scale_command
        )

        if scale_exit_code == 0:
            success_message = f"Deployment '{derived_app_name}' in namespace '{namespace}' successfully scaled to {replicas} replicas on instance '{instance_id}'."
            logger.info(success_message)
            await _append_message_to_chat(chat_request, "assistant", success_message)
            return {
                "status": "success",
                "message": success_message,
                "instance_id": instance_id,
                "namespace": namespace,
                "scaled_replicas": replicas
            }
        else:
            err_msg = f"Failed to scale deployment '{derived_app_name}' on instance '{instance_id}'. Kubectl stderr: {scale_stderr or scale_stdout}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error scaling deployment: {scale_stderr or scale_stdout}")
            return {"status": "error", "message": err_msg, "instance_id": instance_id}

    except FileNotFoundError as fnf_err:
        logger.error(f"Configuration error during scale for {instance_id}: {str(fnf_err)}", exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {str(fnf_err)}")
        return {"status": "error", "message": str(fnf_err), "instance_id": instance_id}
    except ValueError as val_err: # Handles missing EC2_PRIVATE_KEY_BASE_PATH
        logger.error(f"Value error during scale for {instance_id}: {str(val_err)}", exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {str(val_err)}")
        return {"status": "error", "message": str(val_err), "instance_id": instance_id}
    except Exception as e:
        err_msg = f"An unexpected error occurred during cloud-local scale for instance '{instance_id}': {str(e)}"
        logger.error(err_msg, exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {err_msg}")
        return {"status": "error", "message": err_msg, "instance_id": instance_id}


async def handle_cloud_hosted_decommission(
    cluster_name: str,
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest
) -> Dict[str, Any]:
    workspace_dir_path = pathlib.Path(settings.PERSISTENT_WORKSPACE_BASE_DIR) / "cloud-hosted" / cluster_name

    logger.info(f"Initiating decommission for cloud-hosted EKS cluster '{cluster_name}' in workspace: {workspace_dir_path}")
    await _append_message_to_chat(chat_request, "assistant", f"Starting decommission process for EKS cluster: {cluster_name}...")

    if not workspace_dir_path.exists() or not workspace_dir_path.is_dir():
        err_msg = f"Workspace for EKS cluster '{cluster_name}' not found at {workspace_dir_path}. Cannot decommission."
        logger.error(err_msg)
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        return {"status": "error", "mode": "cloud-hosted", "action": "decommission", "instance_id": cluster_name, "message": "Workspace not found for decommission."}

    aws_env_vars: Optional[Dict[str, str]] = None
    if aws_creds:
        aws_env_vars = {
            "AWS_ACCESS_KEY_ID": aws_creds.aws_access_key_id.get_secret_value(),
            "AWS_SECRET_ACCESS_KEY": aws_creds.aws_secret_access_key.get_secret_value(),
            "AWS_DEFAULT_REGION": aws_creds.aws_region,
        }
        logger.info(f"AWS environment variables prepared for EKS decommission. Region: {aws_creds.aws_region}.")
    else:
        # This should ideally be caught by the router if credentials are required
        err_msg = "AWS credentials are required for cloud-hosted decommission but were not provided."
        logger.error(err_msg)
        await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
        return {"status": "error", "mode": "cloud-hosted", "action": "decommission", "instance_id": cluster_name, "message": err_msg}

    await _append_message_to_chat(chat_request, "assistant", "Initializing Terraform for the EKS workspace...")
    init_ok, init_out, init_err = await asyncio.to_thread(
        terraform_service.run_terraform_init, str(workspace_dir_path), aws_env_vars
    )
    logger.info(f"Terraform init stdout for EKS decommission of '{cluster_name}':\n{init_out}")
    if init_err: logger.error(f"Terraform init stderr for EKS decommission of '{cluster_name}':\n{init_err}")

    if not init_ok:
        err_msg = f"Error during Terraform init for EKS workspace: {init_err or init_out}"
        await _append_message_to_chat(chat_request, "assistant", err_msg)
        return {"status": "error", "mode": "cloud-hosted", "action": "decommission", "instance_id": cluster_name, "message": f"Terraform init failed: {init_err or init_out}"}

    await _append_message_to_chat(chat_request, "assistant", f"Executing Terraform destroy for EKS cluster {cluster_name}. This may take a significant amount of time (15-25 minutes)...")
    destroy_ok, destroy_out, destroy_err = await asyncio.to_thread(
        terraform_service.run_terraform_destroy, str(workspace_dir_path), aws_env_vars
    )
    logger.info(f"Terraform destroy stdout for EKS '{cluster_name}':\n{destroy_out}")
    if destroy_err: logger.error(f"Terraform destroy stderr for EKS '{cluster_name}':\n{destroy_err}")

    if destroy_ok:
        await _append_message_to_chat(chat_request, "assistant", f"Terraform destroy successful for EKS cluster {cluster_name}.")
        try:
            await asyncio.to_thread(shutil.rmtree, str(workspace_dir_path))
            logger.info(f"Successfully cleaned up workspace: {workspace_dir_path}")
            final_msg = f"EKS cluster {cluster_name} decommissioned and workspace cleaned."
            await _append_message_to_chat(chat_request, "assistant", final_msg)
            return {"status": "success", "mode": "cloud-hosted", "action": "decommission", "instance_id": cluster_name, "message": final_msg}
        except OSError as e:
            err_msg_cleanup = f"EKS cluster {cluster_name} decommissioned, but failed to clean up persistent workspace: {str(e)}"
            logger.error(f"Failed to clean up workspace {workspace_dir_path}: {e}")
            await _append_message_to_chat(chat_request, "assistant", err_msg_cleanup)
            return {"status": "success_with_cleanup_error", "mode": "cloud-hosted", "action": "decommission", "instance_id": cluster_name, "message": err_msg_cleanup}
    else:
        err_msg = f"Error: Terraform destroy failed for EKS cluster {cluster_name}. Details: {destroy_err or destroy_out}"
        await _append_message_to_chat(chat_request, "assistant", err_msg)
        return {"status": "error", "mode": "cloud-hosted", "action": "decommission", "instance_id": cluster_name, "message": f"Terraform destroy failed: {destroy_err or destroy_out}"}


async def handle_cloud_hosted_redeploy(
    cluster_name: str,
    repo_url: str,
    namespace: str,
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest,
    branch: Optional[str] = None
) -> Dict[str, Any]:
    logger.info(f"Handling cloud-hosted redeploy for EKS cluster '{cluster_name}', repo '{repo_url}', namespace '{namespace}', branch '{branch}'.")
    branch_msg = f" on branch '{branch}'" if branch else ""
    await _append_message_to_chat(chat_request, "assistant", f"Starting redeploy process for application in EKS cluster '{cluster_name}' using repository '{repo_url}'{branch_msg}...")

    workspace_dir_path = pathlib.Path(settings.PERSISTENT_WORKSPACE_BASE_DIR) / "cloud-hosted" / cluster_name
    kubeconfig_file_path = workspace_dir_path / f"kubeconfig_{cluster_name}.yaml"

    if not kubeconfig_file_path.exists():
        err_msg = f"Kubeconfig for EKS cluster '{cluster_name}' not found at {kubeconfig_file_path}. Cannot redeploy."
        logger.error(err_msg)
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        return {"status": "error", "mode": "cloud-hosted", "action": "redeploy", "instance_id": cluster_name, "message": "Kubeconfig not found."}

    repo_name_from_url = repo_url.split('/')[-1].replace('.git', '')
    app_name = repo_name_from_url.lower().replace('_','-') # Consistent app name

    # Derive unique_id_part from cluster_name
    cluster_name_parts = cluster_name.split('-')
    unique_id_part = ""
    if len(cluster_name_parts) > 3 and cluster_name_parts[0] == 'app' and cluster_name_parts[1] == 'eks':
        unique_id_part = cluster_name_parts[-1]
        # Potentially validate derived repo part against repo_name_from_url if strictness is needed
        derived_repo_part_from_cluster = "-".join(cluster_name_parts[2:-1])
        if derived_repo_part_from_cluster != repo_name_from_url.replace('_', '-'): # Ensure consistency
             logger.warning(f"App name part derived from cluster_name ('{derived_repo_part_from_cluster}') does not match current repo_name_from_url ('{repo_name_from_url}'). This might indicate issues if the ECR repo was named using a different base.")
             # For now, we will proceed with the unique_id_part derived, but this could be a point of failure if the ECR repo naming is inconsistent.
    else:
        logger.warning(f"Could not reliably derive unique_id_part from cluster_name {cluster_name} for ECR naming. Attempting to use a generic ECR name. This might fail if the ECR repository was not named this way during initial deployment.")
        # Fallback: if unique_id_part is essential and cannot be derived, this is an issue.
        # For this implementation, we construct the ECR name based on the current app_name and the derived unique_id_part.
        # If unique_id_part is empty, the ECR name might not be unique or match the one created.

    # Construct ECR repo name - this MUST match the name used during initial deployment.
    # The initial deployment uses: ecr_repo_name = f"{settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{repo_name_from_url_original}-{unique_id_part_original}"
    # We assume repo_name_from_url for redeploy IS the same as original for ECR naming.
    ecr_repo_name_for_redeploy = f"{settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{app_name}-{unique_id_part}" if unique_id_part else f"{settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{app_name}"
    logger.info(f"Using ECR repository name for redeploy: {ecr_repo_name_for_redeploy}")

    clone_workspace_path = pathlib.Path(tempfile.mkdtemp(prefix="app_redeploy_clone_"))
    pushed_image_uri_redeploy = None
    try:
        await _append_message_to_chat(chat_request, "assistant", f"Cloning repository {repo_url}{' on branch '+branch if branch else ''} locally...")
        cloned_repo_details = await asyncio.to_thread(
            git_service.clone_repository,
            repo_url,
            str(clone_workspace_path),
            branch=branch
        )
        if not cloned_repo_details or not cloned_repo_details.get("success"):
            raise Exception(f"Git clone failed: {cloned_repo_details.get('error', 'Unknown error')}")

        actual_cloned_path = clone_workspace_path / repo_name_from_url
        if not actual_cloned_path.exists() or not actual_cloned_path.is_dir():
            actual_cloned_path = clone_workspace_path # If cloned directly into the temp dir
            if not actual_cloned_path.exists() or not actual_cloned_path.is_dir():
                 raise Exception(f"Cloned repository directory not found at expected path: {clone_workspace_path / repo_name_from_url} or {clone_workspace_path}")
        logger.info(f"Repository cloned to {actual_cloned_path}")

        new_image_version_tag = str(int(time.time()))
        local_image_tag = f"{app_name}-app:{new_image_version_tag}"
        await _append_message_to_chat(chat_request, "assistant", f"Building Docker image {local_image_tag} locally...")
        build_result = await asyncio.to_thread(docker_service.build_docker_image_locally, actual_cloned_path, local_image_tag)
        if not build_result.get("success"):
            raise Exception(f"Docker build failed: {build_result.get('error', 'Unknown error')} Logs: {build_result.get('logs', 'No logs available.')}")

        await _append_message_to_chat(chat_request, "assistant", "Authenticating with AWS ECR...")
        login_details = await asyncio.to_thread(docker_service.get_ecr_login_details, aws_creds.aws_region, aws_creds.aws_access_key_id.get_secret_value(), aws_creds.aws_secret_access_key.get_secret_value())
        if not login_details:
            raise Exception("Failed to get ECR login credentials.")

        ecr_username, ecr_password, ecr_registry_from_token = login_details
        docker_client = docker.from_env()
        login_ok = await asyncio.to_thread(docker_service.login_to_ecr, docker_client, ecr_registry_from_token, ecr_username, ecr_password)
        if not login_ok:
            raise Exception("ECR login failed.")

        await _append_message_to_chat(chat_request, "assistant", f"Pushing image {local_image_tag} to ECR repository {ecr_repo_name_for_redeploy}...")
        pushed_image_uri_redeploy = await asyncio.to_thread(docker_service.push_image_to_ecr, docker_client, local_image_tag, ecr_repo_name_for_redeploy, ecr_registry_from_token, image_version_tag=new_image_version_tag)
        if not pushed_image_uri_redeploy:
            raise Exception("Failed to push image to ECR.")
        await _append_message_to_chat(chat_request, "assistant", f"New image pushed to ECR: {pushed_image_uri_redeploy}")

    except Exception as e:
        logger.error(f"Error during image build/push for redeploy of cluster '{cluster_name}': {e}", exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Error during image build/push: {str(e)}")
        return {"status": "error", "mode": "cloud-hosted", "action": "redeploy", "instance_id": cluster_name, "message": str(e)}
    finally:
        if clone_workspace_path.exists():
            try:
                await asyncio.to_thread(shutil.rmtree, str(clone_workspace_path))
                logger.info(f"Cleaned up temporary clone workspace: {clone_workspace_path}")
            except Exception as e_clean:
                logger.error(f"Failed to cleanup temporary clone workspace {clone_workspace_path} for redeploy: {e_clean}", exc_info=True)

    if not pushed_image_uri_redeploy:
        err_msg = "Image push failed, cannot proceed with kubectl update."
        logger.error(err_msg) # Should have been caught by the exception block already
        await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
        return {"status": "error", "mode": "cloud-hosted", "action": "redeploy", "instance_id": cluster_name, "message": err_msg}

    # Update K8s Deployment
    container_name = app_name  # Assuming container name in deployment spec matches app_name
    # Using `kubectl set image` for simplicity. A more robust approach might involve patching the deployment manifest and reapplying.
    kubectl_set_image_cmd = ["set", "image", f"deployment/{app_name}", f"{container_name}={pushed_image_uri_redeploy}", "--namespace", namespace]

    await _append_message_to_chat(chat_request, "assistant", f"Updating Kubernetes deployment '{app_name}' in namespace '{namespace}' with new image '{pushed_image_uri_redeploy}'...")

    set_image_result = await asyncio.to_thread(k8s_service._run_kubectl_command, kubectl_set_image_cmd, str(kubeconfig_file_path))

    logger.info(f"Kubectl set image stdout for '{app_name}':\n{set_image_result.stdout}")
    if set_image_result.stderr:
        logger.error(f"Kubectl set image stderr for '{app_name}':\n{set_image_result.stderr}")

    if set_image_result.returncode != 0:
        error_detail = set_image_result.stderr or set_image_result.stdout or "Unknown error from kubectl set image."
        err_msg = f"Error updating Kubernetes deployment: {error_detail}"
        logger.error(f"kubectl set image failed for {app_name}: {error_detail}")
        await _append_message_to_chat(chat_request, "assistant", err_msg)

        # Rollback mechanism
        await _append_message_to_chat(chat_request, "assistant", "Attempting rollback to previous deployment version...")
        rollback_cmd = ["rollout", "undo", f"deployment/{app_name}", "--namespace", namespace]
        rollback_result = await asyncio.to_thread(k8s_service._run_kubectl_command, rollback_cmd, str(kubeconfig_file_path))

        if rollback_result.returncode == 0:
            rollback_msg = f"Rollback successful for {app_name} in namespace {namespace}"
            logger.info(rollback_msg)
            await _append_message_to_chat(chat_request, "assistant", rollback_msg)
            return {"status": "rollback_success", "mode": "cloud-hosted", "action": "redeploy", "instance_id": cluster_name, "message": f"Redeployment failed but rollback successful: {error_detail}"}
        else:
            rollback_err = f"Rollback failed: {rollback_result.stderr or rollback_result.stdout}"
            logger.error(rollback_err)
            await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {rollback_err}")
            return {"status": "error", "mode": "cloud-hosted", "action": "redeploy", "instance_id": cluster_name, "message": f"Redeployment and rollback failed: {error_detail} | Rollback: {rollback_err}"}

    final_success_message = f"Application '{app_name}' in EKS cluster '{cluster_name}' successfully redeployed with image '{pushed_image_uri_redeploy}'. It may take a few moments for the changes to roll out."
    await _append_message_to_chat(chat_request, "assistant", final_success_message)

    return {
        "status": "success",
        "mode": "cloud-hosted",
        "action": "redeploy",
        "instance_id": cluster_name,
        "repo_url": repo_url,
        "redeployed_image_uri": pushed_image_uri_redeploy,
        "message": "Redeployment complete."
    }


async def handle_cloud_hosted_scale(
    cluster_name: str,
    deployment_name_to_scale: Optional[str],
    namespace: str,
    replicas: int,
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest,
    enable_autoscaling: bool = False,
    max_replicas: int = 10,
    cpu_threshold: int = 80
) -> Dict[str, Any]:
    logger.info(f"Cloud-hosted scale: EKS Cluster '{cluster_name}', Deployment '{deployment_name_to_scale}', Namespace '{namespace}', Replicas: {replicas}, Autoscaling: {enable_autoscaling}")
    scaling_type = "autoscaling" if enable_autoscaling else f"manual scaling to {replicas} replicas"
    await _append_message_to_chat(chat_request, "assistant", f"Initiating {scaling_type} for deployment '{deployment_name_to_scale}' in EKS cluster '{cluster_name}'...")

    try:
        if not deployment_name_to_scale:
            err_msg = "Deployment name (expected via 'instance_name' in request) was not provided. Cannot determine which deployment to scale."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
            return {"status": "error", "message": err_msg, "instance_id": cluster_name}

        workspace_dir_path = pathlib.Path(settings.PERSISTENT_WORKSPACE_BASE_DIR) / "cloud-hosted" / cluster_name
        kubeconfig_file_path = workspace_dir_path / f"kubeconfig_{cluster_name}.yaml"

        if not kubeconfig_file_path.exists():
            err_msg = f"Kubeconfig for EKS cluster '{cluster_name}' not found at {kubeconfig_file_path}. Cannot scale."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
            return {"status": "error", "message": err_msg, "instance_id": cluster_name}

        await _append_message_to_chat(chat_request, "assistant", f"Scaling deployment '{deployment_name_to_scale}' to {replicas} replicas...")

        scale_command_list = [
            "scale", "deployment", deployment_name_to_scale,
            f"--replicas={replicas}",
            "--namespace", namespace
        ]

        scale_result = await asyncio.to_thread(
            k8s_service._run_kubectl_command,
            command_args=scale_command_list,
            kubeconfig_path=str(kubeconfig_file_path)
        )

        logger.info(f"Kubectl scale stdout for '{deployment_name_to_scale}':\n{scale_result.stdout}")
        if scale_result.stderr:
            logger.error(f"Kubectl scale stderr for '{deployment_name_to_scale}':\n{scale_result.stderr}")

        if scale_result.returncode == 0:
            success_message = f"Deployment '{deployment_name_to_scale}' in namespace '{namespace}' (EKS cluster '{cluster_name}') successfully scaled to {replicas} replicas."
            logger.info(success_message)
            await _append_message_to_chat(chat_request, "assistant", success_message)

            # Autoscaling implementation
            if enable_autoscaling:
                await _append_message_to_chat(chat_request, "assistant", f"Configuring autoscaling for deployment '{deployment_name_to_scale}' (max: {max_replicas}, CPU: {cpu_threshold}%)...")
                hpa_manifest = manifest_service.generate_hpa_manifest(
                    deployment_name_to_scale,
                    namespace,
                    min_replicas=replicas,
                    max_replicas=max_replicas,
                    cpu_threshold=cpu_threshold
                )

                hpa_file_path = workspace_dir_path / f"{deployment_name_to_scale}_hpa.yaml"
                with open(hpa_file_path, "w") as f:
                    f.write(hpa_manifest)

                apply_hpa_result = await asyncio.to_thread(
                    k8s_service._run_kubectl_command,
                    ["apply", "-f", str(hpa_file_path)],
                    str(kubeconfig_file_path)
                )

                if apply_hpa_result.returncode == 0:
                    hpa_success = f"Autoscaling configured for {deployment_name_to_scale} (max {max_replicas} replicas at {cpu_threshold}% CPU)"
                    await _append_message_to_chat(chat_request, "assistant", hpa_success)
                    success_message += f"\n{hpa_success}"
                else:
                    hpa_error = f"Autoscaling setup failed: {apply_hpa_result.stderr or apply_hpa_result.stdout}"
                    await _append_message_to_chat(chat_request, "assistant", hpa_error)
                    success_message += f"\n{hpa_error}"

            return {
                "status": "success",
                "message": success_message,
                "instance_id": cluster_name,
                "deployment_name": deployment_name_to_scale,
                "namespace": namespace,
                "scaled_replicas": replicas,
                "autoscaling_enabled": enable_autoscaling
            }
        else:
            err_msg = f"Failed to scale deployment '{deployment_name_to_scale}'. Error: {scale_result.stderr or scale_result.stdout}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error scaling deployment: {scale_result.stderr or scale_result.stdout}")
            return {"status": "error", "message": err_msg, "instance_id": cluster_name}

    except FileNotFoundError as fnf_err: # For kubeconfig not found
        logger.error(f"File not found error during cloud-hosted scale for cluster '{cluster_name}': {str(fnf_err)}", exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {str(fnf_err)}")
        return {"status": "error", "message": str(fnf_err), "instance_id": cluster_name}
    except Exception as e:
        err_msg = f"An unexpected error in handle_cloud_local_redeploy for instance '{instance_id}': {str(e)}"
        logger.error(f"An unexpected error in handle_cloud_local_redeploy for instance '{instance_id}': {str(e)}", exc_info=True)
        err_msg = f"An unexpected error in handle_cloud_local_scale for instance '{instance_id}': {str(e)}"
        logger.error(f"An unexpected error in handle_cloud_local_scale for instance '{instance_id}': {str(e)}", exc_info=True)
        err_msg = f"An unexpected error in handle_cloud_hosted_scale for cluster '{cluster_name}', deployment '{deployment_name_to_scale}': {str(e)}"
        logger.error(f"An unexpected error in handle_cloud_hosted_scale for cluster '{cluster_name}', deployment '{deployment_name_to_scale}': {str(e)}", exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {err_msg}")
        return {"status": "error", "message": err_msg, "instance_id": cluster_name}
