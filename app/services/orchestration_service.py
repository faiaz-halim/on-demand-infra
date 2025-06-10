import logging
from typing import Optional, Dict, Any, List
import asyncio
import tempfile
import pathlib
import shutil
import uuid
import json

from app.core.schemas import AWSCredentials, ChatCompletionRequest, ChatMessage
from app.core.config import settings
from app.services import terraform_service
from app.services import ssh_service
from app.services import manifest_service

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
    """Handles the local deployment workflow. Placeholder for actual implementation."""
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
    """Handles the cloud-local deployment workflow: EC2 with Kind, then app deployment."""
    repo_name_part = repo_url.split('/')[-1].replace('.git', '').replace('.', '-')
    unique_id = uuid.uuid4().hex[:6]
    instance_name_tag = f"mcp-cl-{repo_name_part}-{unique_id}"
    sg_name = f"{instance_name_tag}-sg"
    app_name = repo_name_part.lower().replace('_', '-')

    logger.info(f"Cloud-local deployment: provisioning EC2 instance '{instance_name_tag}' for repo '{repo_url}' to deploy app '{app_name}' in K8s namespace '{namespace}'.")
    await _append_message_to_chat(chat_request, "assistant", f"Initiating cloud-local deployment for {repo_url}. App: {app_name}, Instance: {instance_name_tag}, K8s Namespace: {namespace}")

    tf_workspace_dir_path = pathlib.Path(tempfile.mkdtemp(prefix="mcp_tf_cl_"))
    logger.info(f"Created temporary Terraform workspace: {tf_workspace_dir_path}")

    local_manifest_temp_dir_path: Optional[pathlib.Path] = None

    ec2_key_name_to_use = chat_request.ec2_key_name or settings.EC2_DEFAULT_KEY_NAME
    if not ec2_key_name_to_use:
        err_msg = "EC2 Key Name is not configured/provided."
        logger.error(err_msg)
        await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
        return {"status": "error", "mode": "cloud-local", "message": err_msg}

    # Determine NodePort for the service - this port needs to be opened in EC2 SG
    # For simplicity, derive from app_name. Ensure it's within valid NodePort range (typically 30000-32767)
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
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        await _append_message_to_chat(chat_request, "assistant", "Generating Terraform configuration for EC2...")

        # Define the application ports for the Security Group, including the NodePort
        tf_app_ports = [{'port': node_port_num, 'protocol': 'tcp'}]
        # If there are other standard ports (like 80/443 for an LB later, they could be added here too)
        # For now, just the NodePort for direct access to the service on EC2.

        tf_context = {
            "aws_region": aws_creds.aws_region, "ami_id": settings.EC2_DEFAULT_AMI_ID,
            "instance_type": settings.EC2_DEFAULT_INSTANCE_TYPE, "key_name": ec2_key_name_to_use,
            "instance_name_tag": instance_name_tag, "sg_name": sg_name,
            "ssh_cidr": "0.0.0.0/0",
            "app_ports": tf_app_ports, # Use the list with the NodePort for SG
            "user_data_content": bootstrap_content,
        }
        tf_file_path_str = await asyncio.to_thread(terraform_service.generate_ec2_tf_config, tf_context, str(tf_workspace_dir_path))
        if tf_file_path_str is None:
            err_msg = "Failed to generate Terraform EC2 configuration."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        await _append_message_to_chat(chat_request, "assistant", f"Initializing Terraform at {tf_workspace_dir_path}...")
        init_ok, init_out, init_err = await asyncio.to_thread(terraform_service.run_terraform_init, str(tf_workspace_dir_path), aws_env_vars)
        if not init_ok:
            err_msg = f"Terraform init failed: {init_err or init_out}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        await _append_message_to_chat(chat_request, "assistant", "Applying Terraform to provision EC2 (may take minutes)...")
        apply_ok, outputs, apply_out, apply_err = await asyncio.to_thread(terraform_service.run_terraform_apply, str(tf_workspace_dir_path), aws_env_vars)
        if not apply_ok:
            err_msg = f"Terraform apply failed: {apply_err or apply_out}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error during Terraform Apply: {err_msg}. Attempting to clean up resources...")
            destroy_ok, destroy_out, destroy_err = await asyncio.to_thread(terraform_service.run_terraform_destroy, str(tf_workspace_dir_path), aws_env_vars)
            log_destroy_msg = f"Terraform destroy attempt after apply failure: Success={destroy_ok}\nstdout:\n{destroy_out}\nstderr:\n{destroy_err}"
            logger.info(log_destroy_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Resource cleanup attempt: {log_destroy_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        public_ip = outputs.get("public_ip", "Not available")
        instance_id = outputs.get("instance_id", "Not available")
        await _append_message_to_chat(chat_request, "assistant", f"EC2 instance '{instance_name_tag}' provisioned! IP: {public_ip}, ID: {instance_id}.")

        if public_ip == "Not available":
            err_msg = "EC2 Public IP not available from Terraform outputs."
            # ... (error handling)
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        if not settings.EC2_PRIVATE_KEY_BASE_PATH:
            err_msg = "Server configuration error: EC2_PRIVATE_KEY_BASE_PATH not set."
            # ... (error handling)
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        private_key_full_path = str(pathlib.Path(settings.EC2_PRIVATE_KEY_BASE_PATH) / ec2_key_name_to_use)
        if not pathlib.Path(private_key_full_path).exists():
            err_msg = f"SSH private key '{ec2_key_name_to_use}' not found at {private_key_full_path}."
            # ... (error handling)
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        remote_repo_path = f"{settings.EC2_DEFAULT_REPO_PATH}/{app_name}"
        clone_command = f"sudo rm -rf {remote_repo_path} && git clone --depth 1 {repo_url} {remote_repo_path}"
        await _append_message_to_chat(chat_request, "assistant", f"Cloning repository {repo_url} on EC2...")
        await asyncio.sleep(15)
        clone_stdout, clone_stderr, clone_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, clone_command)
        if clone_exit_code != 0:
            err_msg = f"Failed to clone repo on EC2: {clone_stderr or clone_stdout}"
            # ... (error handling)
            return {"status": "error", "mode": "cloud-local", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", f"Repository cloned to {remote_repo_path} on EC2.")

        built_image_tag = f"{app_name}:latest"
        docker_build_command = f"cd {remote_repo_path} && sudo docker build -t {built_image_tag} ."
        await _append_message_to_chat(chat_request, "assistant", f"Building Docker image {built_image_tag} on EC2...")
        build_stdout, build_stderr, build_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, docker_build_command)
        if build_exit_code != 0:
            err_msg = f"Failed to build Docker image on EC2: {build_stderr or build_stdout}"
            # ... (error handling)
            return {"status": "error", "mode": "cloud-local", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", f"Docker image {built_image_tag} built on EC2.")

        kind_cluster_name_on_ec2 = settings.KIND_CLUSTER_NAME
        load_image_command = f"sudo kind load docker-image {built_image_tag} --name {kind_cluster_name_on_ec2}"
        await _append_message_to_chat(chat_request, "assistant", f"Loading image {built_image_tag} into Kind cluster '{kind_cluster_name_on_ec2}' on EC2...")
        load_stdout, load_stderr, load_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, load_image_command)
        if load_exit_code != 0:
            err_msg = f"Failed to load image into Kind on EC2: {load_stderr or load_stdout}"
            # ... (error handling)
            return {"status": "error", "mode": "cloud-local", "message": err_msg}
        await _append_message_to_chat(chat_request, "assistant", "Image loaded into Kind successfully.")

        await _append_message_to_chat(chat_request, "assistant", "Generating Kubernetes manifests...")
        container_port = settings.EC2_DEFAULT_APP_PORTS[0]['port'] if settings.EC2_DEFAULT_APP_PORTS else 80

        deployment_yaml = manifest_service.generate_deployment_manifest(
            image_name=built_image_tag, app_name=app_name, replicas=1,
            ports=[container_port], namespace=namespace
        )
        service_yaml = manifest_service.generate_service_manifest(
            app_name=app_name, service_type="NodePort",
            ports_mapping=[{'port': 80, 'targetPort': container_port, 'nodePort': node_port_num}],
            namespace=namespace
        )
        if not deployment_yaml or not service_yaml:
            err_msg = "Failed to generate Kubernetes manifests locally."
            # ... (error handling)
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        local_manifest_temp_dir_path = pathlib.Path(tempfile.mkdtemp(prefix="mcp_manifests_local_"))
        logger.info(f"Created local temp manifest dir: {local_manifest_temp_dir_path}")
        try:
            with open(local_manifest_temp_dir_path / "deployment.yaml", "w") as f: f.write(deployment_yaml)
            with open(local_manifest_temp_dir_path / "service.yaml", "w") as f: f.write(service_yaml)

            remote_manifest_dir = settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH
            mkdir_command = f"mkdir -p {remote_manifest_dir}"
            logger.info(f"Creating remote manifest directory '{remote_manifest_dir}' on EC2...")
            _, mkdir_err, mkdir_exit = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, mkdir_command)
            if mkdir_exit != 0:
                err_msg = f"Failed to create remote manifest directory '{remote_manifest_dir}' on EC2: {mkdir_err}"
                # ... (error handling)
                return {"status": "error", "mode": "cloud-local", "message": err_msg}

            await _append_message_to_chat(chat_request, "assistant", f"Uploading manifests to {remote_manifest_dir} on EC2...")
            upload_dep_ok = await asyncio.to_thread(ssh_service.upload_file_sftp, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, str(local_manifest_temp_dir_path / "deployment.yaml"), f"{remote_manifest_dir}/deployment.yaml")
            upload_svc_ok = await asyncio.to_thread(ssh_service.upload_file_sftp, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, str(local_manifest_temp_dir_path / "service.yaml"), f"{remote_manifest_dir}/service.yaml")
            if not upload_dep_ok or not upload_svc_ok:
                err_msg = "Failed to upload manifests to EC2."
                # ... (error handling)
                return {"status": "error", "mode": "cloud-local", "message": err_msg}
        finally:
            shutil.rmtree(local_manifest_temp_dir_path)
            logger.info(f"Cleaned up local temp manifest dir: {local_manifest_temp_dir_path}")

        apply_command = f"sudo kubectl apply --namespace {namespace} -f {remote_manifest_dir}/"
        await _append_message_to_chat(chat_request, "assistant", "Applying Kubernetes manifests on EC2...")
        apply_m_stdout, apply_m_stderr, apply_m_exit_code = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, apply_command)
        if apply_m_exit_code != 0:
            err_msg = f"Failed to apply K8s manifests on EC2: {apply_m_stderr or apply_m_stdout}"
            # ... (error handling)
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        cleanup_remote_cmd = f"rm -rf {remote_manifest_dir}"
        # ... (cleanup remote dir) ...
        _, _, cleanup_exit = await asyncio.to_thread(ssh_service.execute_remote_command, public_ip, settings.EC2_SSH_USERNAME, private_key_full_path, cleanup_remote_cmd)
        if cleanup_exit != 0: logger.warning(f"Failed to cleanup remote manifests dir {remote_manifest_dir} on EC2.")
        else: logger.info("Remote manifests directory cleaned up.")

        app_url = f"http://{public_ip}:{node_port_num}"
        final_success_message = f"Application '{app_name}' deployed to Kind on EC2 instance '{instance_name_tag}' (IP: {public_ip}) in namespace '{namespace}'. Application may be accessible at: {app_url}"
        await _append_message_to_chat(chat_request, "assistant", final_success_message)

        return {"status": "success", "mode": "cloud-local", "message": final_success_message, "outputs": outputs, "ec2_instance_name": instance_name_tag, "ec2_public_ip": public_ip, "built_image_tag": built_image_tag, "app_url": app_url}

    except Exception as e:
        err_msg = f"An unexpected error in handle_cloud_local_deployment: {str(e)}"
        logger.error(err_msg, exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {err_msg}")
        return {"status": "error", "mode": "cloud-local", "message": err_msg}
    finally:
        if tf_workspace_dir_path.exists():
            try:
                shutil.rmtree(str(tf_workspace_dir_path))
                logger.info(f"Cleaned up temporary Terraform workspace: {tf_workspace_dir_path}")
            except Exception as e:
                logger.error(f"Failed to cleanup temporary Terraform workspace {tf_workspace_dir_path}: {e}", exc_info=True)
        if local_manifest_temp_dir_path and local_manifest_temp_dir_path.exists():
             shutil.rmtree(local_manifest_temp_dir_path)


async def handle_cloud_hosted_deployment(
    repo_url: str,
    namespace: str,
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest
):
    """Handles the cloud-hosted (EKS) deployment workflow. Placeholder."""
    logger.info(f"Cloud-hosted (EKS) deployment requested for repo '{repo_url}' in namespace '{namespace}'.")
    aws_env_vars = {
        "AWS_ACCESS_KEY_ID": aws_creds.aws_access_key_id.get_secret_value(),
        "AWS_SECRET_ACCESS_KEY": aws_creds.aws_secret_access_key.get_secret_value(),
        "AWS_DEFAULT_REGION": aws_creds.aws_region,
    }
    logger.info(f"AWS environment variables prepared for cloud-hosted. Region: {aws_creds.aws_region}.")
    message_content = f"Cloud-hosted (EKS) for {repo_url} in {namespace} (Region: {aws_creds.aws_region}) is under construction."
    await _append_message_to_chat(chat_request, "assistant", message_content)
    return {"status": "pending_feature", "mode": "cloud-hosted", "message": message_content}
