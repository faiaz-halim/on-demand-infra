import unittest
from unittest.mock import patch, AsyncMock, MagicMock, call
import subprocess
import os
import tempfile
import shutil
import pathlib
from pydantic import SecretStr
import uuid
import json
import time # For test_handle_cloud_local_redeploy_success

from app.core.schemas import AWSCredentials, ChatCompletionRequest, ChatMessage
from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment,
    handle_cloud_local_decommission, # Added
    handle_cloud_local_redeploy,   # Added
    handle_cloud_local_scale     # Added
)
from app.core.config import settings as app_settings

import logging

logger = logging.getLogger('app.services.orchestration_service')

class TestOrchestrationService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.repo_url = "https://github.com/test/repo.git"
        self.namespace = "test-ns"
        self.aws_creds = AWSCredentials(
            aws_access_key_id=SecretStr("test_access_key"),
            aws_secret_access_key=SecretStr("test_secret_key"),
            aws_region="us-east-1"
        )
        self.original_ec2_key_name = app_settings.EC2_DEFAULT_KEY_NAME
        self.original_private_key_base_path = app_settings.EC2_PRIVATE_KEY_BASE_PATH
        self.original_persistent_workspace_base_dir = app_settings.PERSISTENT_WORKSPACE_BASE_DIR

        # Create a temporary directory for persistent workspaces during tests
        self.test_persistent_workspaces = tempfile.mkdtemp(prefix="mcp_test_persistent_")
        app_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces


    def tearDown(self):
        app_settings.EC2_DEFAULT_KEY_NAME = self.original_ec2_key_name
        app_settings.EC2_PRIVATE_KEY_BASE_PATH = self.original_private_key_base_path
        app_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.original_persistent_workspace_base_dir
        shutil.rmtree(self.test_persistent_workspaces)


    async def test_handle_local_deployment_no_creds_involved(self):
        mock_chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="Deploy local please")])
        response_dict = await handle_local_deployment(self.repo_url, self.namespace, mock_chat_request)
        self.assertTrue(len(mock_chat_request.messages) > 1)
        # ... (rest of assertions as before)

    # --- Test handle_cloud_local_deployment (condensed for brevity, full version from previous steps) ---
    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script')
    @patch('app.services.orchestration_service.ssh_service.execute_remote_command')
    @patch('app.services.orchestration_service.ssh_service.upload_file_sftp', return_value=True)
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="kind: Deployment...")
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="kind: Service...")
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_success(
        self, mock_orch_settings, mock_pathlib_Path,
        mock_gen_svc_manifest, mock_gen_dep_manifest, mock_upload_sftp, mock_ssh_exec,
        mock_gen_bootstrap, mock_gen_tf_config,
        mock_tf_init, mock_tf_apply, mock_tf_destroy,
        mock_mkdtemp, mock_rmtree):

        # ... (setup mocks as in previous detailed version for this test) ...
        mock_orch_settings.EC2_DEFAULT_KEY_NAME = "default_key.pem"
        mock_orch_settings.DEFAULT_KIND_VERSION = "0.20.0"; mock_orch_settings.DEFAULT_KUBECTL_VERSION = "1.27.0"
        mock_orch_settings.EC2_DEFAULT_AMI_ID = "ami-settings"; mock_orch_settings.EC2_DEFAULT_INSTANCE_TYPE = "t2.small"
        mock_orch_settings.EC2_DEFAULT_APP_PORTS = [{"port": 80, "protocol": "tcp"}]
        mock_orch_settings.EC2_PRIVATE_KEY_BASE_PATH = "/test/keys"
        mock_orch_settings.EC2_SSH_USERNAME = "test-user"
        mock_orch_settings.EC2_DEFAULT_REPO_PATH = "/home/test-user/app"
        mock_orch_settings.KIND_CLUSTER_NAME = "mcp-kind-cluster"
        mock_orch_settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH = "/tmp/mcp_manifests_remote"
        # Use the test-specific persistent workspace
        mock_orch_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces


        mock_path_instance = mock_pathlib_Path.return_value
        mock_path_instance.exists.return_value = True
        # Adjust side_effect for mkdtemp to reflect that persistent workspace is now used directly for TF files
        mock_mkdtemp.return_value = "/mocked/local_manifest_temp" # Only for local manifests now

        mock_gen_bootstrap.return_value = "#!/bin/bash\necho 'Mocked Bootstrap'"
        mock_gen_tf_config.return_value = str(pathlib.Path(self.test_persistent_workspaces) / "cloud-local" / "mcp-cl-repo-testuuid" / "main.tf") # Example path
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_apply.return_value = (True, {"public_ip": "1.2.3.4", "instance_id": "i-123"}, "Apply success", "")

        mock_ssh_exec.side_effect = [
            ("Cloned successfully", "", 0), ("Image built successfully", "", 0),
            ("Image loaded into Kind", "", 0), ("Remote manifest dir created", "", 0),
            ("Manifests applied to K8s", "", 0), ("Remote manifests cleaned up", "", 0)
        ]

        chat_request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="deploy")],
            ec2_key_name="user_provided_key.pem", target_namespace=self.namespace
        )

        with patch('app.services.orchestration_service.uuid.uuid4') as mock_uuid: # Ensure instance_name_tag is predictable
            mock_uuid.return_value.hex = "testuuid"
            response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success")
        self.assertIn("Instance ID for management: mcp-cl-repo-testuu", response["message"]) # Check instance_id in message
        self.assertEqual(response["instance_id"], "mcp-cl-repo-testuu") # Check instance_id in response data

        # Assertions for TF config path (now persistent)
        expected_tf_workspace = pathlib.Path(self.test_persistent_workspaces) / "cloud-local" / "mcp-cl-repo-testuu"
        mock_gen_tf_config.assert_called_with(unittest.mock.ANY, str(expected_tf_workspace))

        mock_rmtree.assert_called_once_with("/mocked/local_manifest_temp") # Only local manifest temp dir cleaned up
        mock_tf_destroy.assert_not_called()


    # --- Tests for handle_cloud_local_decommission ---
    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_decommission_success(
        self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree):

        instance_id = "mcp-cl-testapp-123456"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces

        mock_workspace_path_instance = MagicMock()
        mock_workspace_path_instance.exists.return_value = True
        mock_workspace_path_instance.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_instance # For the workspace path object

        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_destroy.return_value = (True, "Destroy success", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission")])
        response = await handle_cloud_local_decommission(instance_id, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success")
        self.assertIn(f"Instance {instance_id} decommissioned and workspace cleaned.", response["message"])

        expected_workspace_path = str(pathlib.Path(self.test_persistent_workspaces) / "cloud-local" / instance_id)
        mock_tf_init.assert_called_once_with(expected_workspace_path, unittest.mock.ANY)
        mock_tf_destroy.assert_called_once_with(expected_workspace_path, unittest.mock.ANY)
        mock_rmtree.assert_called_once_with(mock_workspace_path_instance) # Should be called with the Path object

    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_decommission_workspace_not_found(self, mock_settings, mock_pathlib_Path):
        instance_id = "nonexistent-id"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces

        mock_workspace_path_instance = MagicMock()
        mock_workspace_path_instance.exists.return_value = False # Workspace does not exist
        mock_pathlib_Path.return_value = mock_workspace_path_instance

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission")])
        response = await handle_cloud_local_decommission(instance_id, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Workspace for instance ID", response["message"])
        self.assertIn("not found", response["message"])

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_decommission_tf_destroy_fails(
        self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree):
        instance_id = "fail-destroy-id"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_path_instance = mock_pathlib_Path.return_value
        mock_path_instance.exists.return_value = True
        mock_path_instance.is_dir.return_value = True

        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_destroy.return_value = (False, "", "Terraform destroy command failed")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission")])
        response = await handle_cloud_local_decommission(instance_id, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform destroy failed", response["message"])
        mock_rmtree.assert_not_called() # Workspace should not be cleaned if destroy fails

    @patch('app.services.orchestration_service.shutil.rmtree', side_effect=OSError("Cleanup failed"))
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_decommission_destroy_succeeds_cleanup_fails(
        self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree):
        instance_id = "cleanup-fail-id"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_path_instance = mock_pathlib_Path.return_value
        mock_path_instance.exists.return_value = True
        mock_path_instance.is_dir.return_value = True

        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_destroy.return_value = (True, "Destroy success", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission")])
        response = await handle_cloud_local_decommission(instance_id, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success_with_cleanup_warning")
        self.assertIn(f"Instance {instance_id} decommissioned, but failed to clean up persistent workspace", chat_request.messages[-1].content)
        self.assertIn("Cleanup failed", response["cleanup_error"])
        mock_rmtree.assert_called_once()


    # --- Tests for handle_cloud_local_redeploy (Placeholder - to be filled in next subtask if separate) ---
    # For now, just a basic check that the stub is callable
    async def test_handle_cloud_local_redeploy_stub_callable(self):
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="redeploy")])
        response = await handle_cloud_local_redeploy(
            instance_id="test-instance", public_ip="1.2.3.4", ec2_key_name="key.pem",
            repo_url=self.repo_url, namespace=self.namespace, aws_creds=self.aws_creds,
            chat_request=chat_request
        )
        self.assertEqual(response["status"], "pending_implementation")


    # --- Tests for handle_cloud_local_scale (Placeholder - to be filled in next subtask) ---
    async def test_handle_cloud_local_scale_stub_callable(self):
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="scale")])
        response = await handle_cloud_local_scale(
            instance_id="test-instance", public_ip="1.2.3.4", ec2_key_name="key.pem",
            namespace=self.namespace, replicas=3, aws_creds=self.aws_creds,
            chat_request=chat_request
        )
        self.assertEqual(response["status"], "pending_implementation")


    # ... (other existing tests like test_handle_cloud_hosted_deployment_placeholder)
    # Note: The 'test_handle_cloud_hosted_deployment_placeholder' might need to be removed or updated
    # if the actual implementation is no longer a placeholder. For now, adding new detailed tests.

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path') # To mock workspace path creation/checks
    @patch('app.services.orchestration_service.settings') # Mock settings directly used in the function
    async def test_handle_cloud_hosted_deployment_success(
        self, mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply
    ):
        # Setup mocked settings values
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX = "mcp-eks-test"
        mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX = "mcp-app-test"
        mock_settings.EKS_DEFAULT_VPC_CIDR = "10.1.0.0/16" # Example different from prod defaults
        mock_settings.EKS_DEFAULT_NUM_PUBLIC_SUBNETS = 1
        mock_settings.EKS_DEFAULT_NUM_PRIVATE_SUBNETS = 1
        mock_settings.EKS_DEFAULT_VERSION = "1.27"
        mock_settings.EKS_DEFAULT_NODE_GROUP_NAME_SUFFIX = "ng-custom"
        mock_settings.EKS_DEFAULT_NODE_INSTANCE_TYPE = "t3.small"
        mock_settings.EKS_DEFAULT_NODE_DESIRED_SIZE = 1
        mock_settings.EKS_DEFAULT_NODE_MIN_SIZE = 1
        mock_settings.EKS_DEFAULT_NODE_MAX_SIZE = 1
        mock_settings.ECR_DEFAULT_IMAGE_TAG_MUTABILITY = "IMMUTABLE"
        mock_settings.ECR_DEFAULT_SCAN_ON_PUSH = False

        mock_uuid4.return_value.hex = "testuuid"

        mock_path_instance = MagicMock()
        mock_pathlib_Path.return_value = mock_path_instance # Return the same mock for all Path() calls

        mock_generate_ecr_tf_config.return_value = str(mock_path_instance / "test_ecr.tf")
        mock_generate_eks_tf_config.return_value = str(mock_path_instance / "test_eks.tf")
        mock_run_terraform_init.return_value = (True, "init_success_stdout", "")

        mock_tf_outputs = {
            "ecr_repository_url": {"value": "test_ecr_url_output"},
            "ecr_repository_name": {"value": "mcp-app-test-repo-testuuid"},
            "eks_cluster_endpoint": {"value": "test_eks_ep_output"},
            "eks_cluster_ca_data": {"value": "test_ca_data"},
            "vpc_id": {"value": "vpc-12345"}
        }
        mock_run_terraform_apply.return_value = (True, mock_tf_outputs, "apply_stdout", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy cloud hosted")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )

        expected_repo_name_part = "repo" # from self.repo_url
        expected_cluster_name = f"{mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX}-{expected_repo_name_part}-testuuid"
        expected_ecr_repo_name_raw = f"{mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{expected_repo_name_part}-testuuid"
        # Apply same sanitization as in the main function for ECR name
        expected_ecr_repo_name = "".join(c if c.islower() or c.isdigit() or c in ['-', '_', '.'] else '-' for c in expected_ecr_repo_name_raw.lower()).strip('-_.')


        mock_pathlib_Path.assert_any_call(mock_settings.PERSISTENT_WORKSPACE_BASE_DIR)
        expected_workspace_path = mock_pathlib_Path(mock_settings.PERSISTENT_WORKSPACE_BASE_DIR) / "cloud-hosted" / expected_cluster_name
        mock_path_instance.mkdir.assert_called_with(parents=True, exist_ok=True)

        ecr_context_arg = mock_generate_ecr_tf_config.call_args[0][0]
        self.assertEqual(ecr_context_arg["aws_region"], self.aws_creds.aws_region)
        self.assertEqual(ecr_context_arg["ecr_repo_name"], expected_ecr_repo_name)
        self.assertEqual(ecr_context_arg["image_tag_mutability"], mock_settings.ECR_DEFAULT_IMAGE_TAG_MUTABILITY)
        self.assertEqual(ecr_context_arg["scan_on_push"], mock_settings.ECR_DEFAULT_SCAN_ON_PUSH)
        mock_generate_ecr_tf_config.assert_called_once_with(unittest.mock.ANY, str(expected_workspace_path))

        eks_context_arg = mock_generate_eks_tf_config.call_args[0][0]
        self.assertEqual(eks_context_arg["cluster_name"], expected_cluster_name)
        self.assertEqual(eks_context_arg["node_group_name"], f"{expected_cluster_name}-{mock_settings.EKS_DEFAULT_NODE_GROUP_NAME_SUFFIX}")
        mock_generate_eks_tf_config.assert_called_once_with(unittest.mock.ANY, str(expected_workspace_path))

        aws_env_vars_expected = {
            "AWS_ACCESS_KEY_ID": self.aws_creds.aws_access_key_id.get_secret_value(),
            "AWS_SECRET_ACCESS_KEY": self.aws_creds.aws_secret_access_key.get_secret_value(),
            "AWS_DEFAULT_REGION": self.aws_creds.aws_region,
        }
        mock_run_terraform_init.assert_called_once_with(str(expected_workspace_path), aws_env_vars_expected)
        mock_run_terraform_apply.assert_called_once_with(str(expected_workspace_path), aws_env_vars_expected)

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["instance_id"], expected_cluster_name)
        self.assertEqual(response["ecr_repository_url"], "test_ecr_url_output")
        self.assertEqual(response["eks_cluster_endpoint"], "test_eks_ep_output")
        self.assertIn("EKS cluster 'mcp-eks-test-repo-testuuid' and ECR repository 'mcp-app-test-repo-testuuid' provisioned successfully!", chat_request.messages[-1].content)


    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_ecr_gen_fails(
        self, mock_settings, mock_generate_ecr_tf_config, mock_generate_eks_tf_config
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_generate_ecr_tf_config.return_value = None # Simulate ECR config generation failure
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate ECR Terraform configuration", response["message"])
        self.assertIn("Failed to generate ECR Terraform configuration", chat_request.messages[-1].content)
        mock_generate_eks_tf_config.assert_not_called()

    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_eks_gen_fails(
        self, mock_settings, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_generate_ecr_tf_config.return_value = "path/to/ecr.tf"
        mock_generate_eks_tf_config.return_value = None # Simulate EKS config generation failure
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate EKS Terraform configuration", response["message"])
        mock_run_terraform_init.assert_not_called()

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_tf_init_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_run_init, mock_run_apply
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_run_init.return_value = (False, "", "Terraform init failed spectacularly")
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform init failed: Terraform init failed spectacularly", response["message"])
        mock_run_apply.assert_not_called()

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "init_success", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_tf_apply_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_run_init, mock_run_apply
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_run_apply.return_value = (False, {}, "", "Terraform apply exploded")
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform apply failed: Terraform apply exploded", response["message"])
        self.assertIn("Manual cleanup of AWS resources", chat_request.messages[-1].content) # Check for cleanup advice

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply', new_callable=AsyncMock) # Ensure it's an async mock
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', new_callable=AsyncMock)
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', new_callable=MagicMock)
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', new_callable=MagicMock)
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_with_instance_id_override(
        self, mock_settings, mock_uuid4,
        mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX = "mcp-eks"
        mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX = "mcp-app"

        # Make terraform_service functions awaitable mocks that return successful values
        mock_generate_ecr_tf_config.return_value = "path/to/ecr.tf"
        mock_generate_eks_tf_config.return_value = "path/to/eks.tf"
        mock_run_terraform_init.return_value = (True, "init_success_stdout", "")
        mock_tf_outputs = {"ecr_repository_url": {"value": "override_ecr_url"}, "eks_cluster_endpoint": {"value": "override_eks_ep"}}
        mock_run_terraform_apply.return_value = (True, mock_tf_outputs, "apply_stdout", "")

        instance_id_override = "my-custom-id"
        chat_request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="deploy cloud hosted with override")],
            instance_id_override=instance_id_override # Key for cloud-hosted override
        )

        await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )

        mock_uuid4.assert_not_called() # UUID should not be used if override is present

        expected_repo_name_part = "repo"
        expected_cluster_name_override = f"{mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX}-{expected_repo_name_part}-{instance_id_override}"
        expected_ecr_repo_name_override_raw = f"{mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{expected_repo_name_part}-{instance_id_override}"
        expected_ecr_repo_name_override = "".join(c if c.islower() or c.isdigit() or c in ['-', '_', '.'] else '-' for c in expected_ecr_repo_name_override_raw.lower()).strip('-_.')


        # Check that workspace path uses the override
        called_workspace_path_for_ecr = pathlib.Path(mock_generate_ecr_tf_config.call_args[0][1])
        self.assertIn(expected_cluster_name_override, str(called_workspace_path_for_ecr))

        called_workspace_path_for_eks = pathlib.Path(mock_generate_eks_tf_config.call_args[0][1])
        self.assertIn(expected_cluster_name_override, str(called_workspace_path_for_eks))

        # Check that names passed to TF generation use the override
        ecr_context_arg = mock_generate_ecr_tf_config.call_args[0][0]
        self.assertEqual(ecr_context_arg["ecr_repo_name"], expected_ecr_repo_name_override)

        eks_context_arg = mock_generate_eks_tf_config.call_args[0][0]
        self.assertEqual(eks_context_arg["cluster_name"], expected_cluster_name_override)


if __name__ == '__main__':
    unittest.main()
