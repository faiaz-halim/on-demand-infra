import logging
from typing import Optional, Dict, Any, List
import asyncio
import tempfile
import pathlib
import shutil
import uuid
import json # Added for parsing EC2_DEFAULT_APP_PORTS if it were still a JSON string in settings

from app.core.schemas import AWSCredentials, ChatCompletionRequest, ChatMessage
from app.core.config import settings
from app.services import terraform_service

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def _append_message_to_chat(chat_request: ChatCompletionRequest, role: str, content: str):
    """Helper to append messages to the chat request's message list."""
    # Ensure messages list exists if it's Optional and None (though it's not in current ChatCompletionRequest)
    # if chat_request.messages is None:
    #     chat_request.messages = [] # Initialize if it were Optional
    chat_request.messages.append(ChatMessage(role=role, content=content))


async def handle_local_deployment(
    repo_url: str,
    namespace: str,
    chat_request: ChatCompletionRequest
):
    """
    Handles the local deployment workflow.
    Placeholder for actual implementation.
    """
    logger.info(f"Local deployment requested for repo '{repo_url}' in namespace '{namespace}'.")
    logger.debug(f"Full chat request context for local deployment: {chat_request.model_dump_json(indent=2)}")

    message_content = f"Local deployment process started for {repo_url} in namespace {namespace}. Preparing environment..."
    await _append_message_to_chat(chat_request, "assistant", message_content)

    # This is a placeholder response
    return {"status": "success", "mode": "local", "message": message_content}


async def handle_cloud_local_deployment(
    repo_url: str,
    namespace: str, # Namespace for K8s within Kind on EC2
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest
):
    """
    Handles the cloud-local deployment workflow: EC2 with Kind.
    """
    # Generate a unique enough name for the instance and related resources
    # Using the last part of repo_url and a short UUID
    repo_name_part = repo_url.split('/')[-1].replace('.git', '').replace('.', '-')
    unique_id = uuid.uuid4().hex[:6]
    instance_name_tag = f"mcp-cl-{repo_name_part}-{unique_id}"
    sg_name = f"{instance_name_tag}-sg"

    logger.info(f"Cloud-local deployment: provisioning EC2 instance '{instance_name_tag}' for repo '{repo_url}' in K8s namespace '{namespace}'.")
    await _append_message_to_chat(chat_request, "assistant", f"Initiating cloud-local deployment for {repo_url}. Instance: {instance_name_tag}")

    workspace_dir_path_str = tempfile.mkdtemp(prefix="mcp_tf_cl_")
    workspace_dir = pathlib.Path(workspace_dir_path_str)
    logger.info(f"Created temporary Terraform workspace: {workspace_dir}")

    try:
        aws_env_vars = {
            "AWS_ACCESS_KEY_ID": aws_creds.aws_access_key_id.get_secret_value(),
            "AWS_SECRET_ACCESS_KEY": aws_creds.aws_secret_access_key.get_secret_value(),
            "AWS_DEFAULT_REGION": aws_creds.aws_region,
        }
        logger.info(f"AWS environment variables prepared for cloud-local deployment. Region: {aws_creds.aws_region}. (Secrets are not logged).")

        # 1. Bootstrap Script Generation
        await _append_message_to_chat(chat_request, "assistant", "Generating EC2 bootstrap script...")
        bootstrap_context = {
            'kind_version': settings.DEFAULT_KIND_VERSION,
            'kubectl_version': settings.DEFAULT_KUBECTL_VERSION
        }
        bootstrap_content = await asyncio.to_thread(
            terraform_service.generate_ec2_bootstrap_script,
            bootstrap_context,
            output_dir=None
        )
        if bootstrap_content is None:
            err_msg = "Failed to generate EC2 bootstrap script."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        # 2. Terraform Configuration Generation
        await _append_message_to_chat(chat_request, "assistant", "Generating Terraform configuration for EC2 instance...")

        # Determine EC2 Key Name: from request first, then settings. Fallback to error if neither.
        ec2_key_name_to_use = chat_request.ec2_key_name or settings.EC2_DEFAULT_KEY_NAME
        if not ec2_key_name_to_use:
            err_msg = "EC2 Key Name is not configured in settings and not provided in the request. Cannot proceed."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Configuration Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        tf_context = {
            "aws_region": aws_creds.aws_region,
            "ami_id": settings.EC2_DEFAULT_AMI_ID,
            "instance_type": settings.EC2_DEFAULT_INSTANCE_TYPE, # TODO: Allow override from chat_request.instance_size
            "key_name": ec2_key_name_to_use,
            "instance_name_tag": instance_name_tag,
            "sg_name": sg_name,
            "ssh_cidr": "0.0.0.0/0",
            "app_ports": settings.EC2_DEFAULT_APP_PORTS, # This is a property that parses JSON
            "user_data_content": bootstrap_content,
        }
        tf_file_path_str = await asyncio.to_thread(
            terraform_service.generate_ec2_tf_config,
            tf_context,
            str(workspace_dir)
        )
        if tf_file_path_str is None:
            err_msg = "Failed to generate Terraform EC2 configuration."
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        await _append_message_to_chat(chat_request, "assistant", f"Terraform configuration generated. Initializing Terraform at {workspace_dir}...")

        # 3. Terraform Init
        init_ok, init_out, init_err = await asyncio.to_thread(
            terraform_service.run_terraform_init, str(workspace_dir), aws_env_vars
        )
        logger.info(f"Terraform init stdout:\n{init_out}")
        if init_err: logger.error(f"Terraform init stderr:\n{init_err}")
        if not init_ok:
            err_msg = f"Terraform init failed: {init_err or init_out}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error: {err_msg}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        await _append_message_to_chat(chat_request, "assistant", "Terraform initialized. Applying configuration to provision EC2 instance (this may take a few minutes)...")

        # 4. Terraform Apply
        apply_ok, outputs, apply_out, apply_err = await asyncio.to_thread(
            terraform_service.run_terraform_apply, str(workspace_dir), aws_env_vars
        )
        logger.info(f"Terraform apply stdout:\n{apply_out}")
        if apply_err: logger.error(f"Terraform apply stderr:\n{apply_err}")

        if not apply_ok:
            err_msg = f"Terraform apply failed: {apply_err or apply_out}"
            logger.error(err_msg)
            await _append_message_to_chat(chat_request, "assistant", f"Error during Terraform Apply: {err_msg}. Attempting to clean up resources...")

            destroy_ok, destroy_out, destroy_err = await asyncio.to_thread(
                terraform_service.run_terraform_destroy, str(workspace_dir), aws_env_vars
            )
            logger.info(f"Terraform destroy (after apply failure) stdout:\n{destroy_out}")
            if destroy_err: logger.error(f"Terraform destroy (after apply failure) stderr:\n{destroy_err}")
            if destroy_ok:
                await _append_message_to_chat(chat_request, "assistant", "Attempted to clean up any partially created resources due to apply failure: Destroy successful.")
            else:
                await _append_message_to_chat(chat_request, "assistant", f"Attempted to clean up any partially created resources due to apply failure: Destroy command failed: {destroy_err or destroy_out}")
            return {"status": "error", "mode": "cloud-local", "message": err_msg}

        public_ip = outputs.get('public_ip', "Not available")
        instance_id = outputs.get('instance_id', "Not available")
        logger.info(f"Terraform apply successful. Instance ID: {instance_id}, Public IP: {public_ip}")

        success_message = f"Cloud-local EC2 instance '{instance_name_tag}' (ID: {instance_id}) provisioned successfully! Public IP: {public_ip}. Next steps: deploy app to Kind on this EC2."
        await _append_message_to_chat(chat_request, "assistant", success_message)

        return {"status": "success", "mode": "cloud-local", "message": success_message, "outputs": outputs}

    except Exception as e:
        err_msg = f"An unexpected error occurred during cloud-local deployment: {str(e)}"
        logger.error(err_msg, exc_info=True)
        await _append_message_to_chat(chat_request, "assistant", f"Critical Error: {err_msg}")
        return {"status": "error", "mode": "cloud-local", "message": err_msg}
    finally:
        if workspace_dir and workspace_dir.exists():
            try:
                shutil.rmtree(str(workspace_dir))
                logger.info(f"Cleaned up temporary Terraform workspace: {workspace_dir}")
            except Exception as e:
                logger.error(f"Failed to cleanup temporary workspace {workspace_dir}: {e}", exc_info=True)


async def handle_cloud_hosted_deployment(
    repo_url: str,
    namespace: str,
    aws_creds: AWSCredentials,
    chat_request: ChatCompletionRequest
):
    """
    Handles the cloud-hosted (EKS) deployment workflow.
    Placeholder for actual implementation.
    """
    logger.info(f"Cloud-hosted (EKS) deployment requested for repo '{repo_url}' in namespace '{namespace}'.")

    aws_access_key_id_value = aws_creds.aws_access_key_id.get_secret_value()
    aws_secret_access_key_value = aws_creds.aws_secret_access_key.get_secret_value()

    aws_env_vars = {
        "AWS_ACCESS_KEY_ID": aws_access_key_id_value,
        "AWS_SECRET_ACCESS_KEY": aws_secret_access_key_value,
        "AWS_DEFAULT_REGION": aws_creds.aws_region,
    }
    logger.info(f"AWS environment variables prepared for cloud-hosted deployment. Region: {aws_creds.aws_region}. (Secrets are not logged).")

    message_content = f"Cloud-hosted (EKS) deployment process started for {repo_url} in namespace {namespace}. AWS credentials received for region {aws_creds.aws_region}. This feature is under construction."
    await _append_message_to_chat(chat_request, "assistant", message_content)

    return {"status": "pending_feature", "mode": "cloud-hosted", "message": message_content, "aws_region_processed": aws_creds.aws_region}
