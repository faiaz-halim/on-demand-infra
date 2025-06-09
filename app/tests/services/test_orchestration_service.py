import unittest
from unittest.mock import patch, AsyncMock, MagicMock, call # Added call
import subprocess # Added for CompletedProcess
import os # Added for os.environ for aws_env_vars checks
import tempfile # Added
import shutil # Added
import pathlib # Added
from pydantic import SecretStr

from app.core.schemas import AWSCredentials, ChatCompletionRequest, ChatMessage
from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment
)
from app.core.config import settings # To potentially mock settings values

import logging

logger = logging.getLogger('app.services.orchestration_service') # Get specific logger to assert its calls
# logging.disable(logging.INFO) # Optional: Disable logs for cleaner test output

class TestOrchestrationService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_chat_request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="Deploy something")]
        )
        self.repo_url = "https://github.com/test/repo.git"
        self.namespace = "test-ns"
        self.aws_creds = AWSCredentials(
            aws_access_key_id=SecretStr("test_access_key"),
            aws_secret_access_key=SecretStr("test_secret_key"),
            aws_region="us-east-1"
        )
        # Store original settings values to restore them after tests that modify them
        self.original_ec2_key_name = settings.EC2_DEFAULT_KEY_NAME

    def tearDown(self):
        # Restore original settings
        settings.EC2_DEFAULT_KEY_NAME = self.original_ec2_key_name


    async def test_handle_local_deployment_no_creds_involved(self):
        # This test remains largely the same as it's a simple stub for now
        response_dict = await handle_local_deployment(self.repo_url, self.namespace, self.mock_chat_request)

        self.assertTrue(len(self.mock_chat_request.messages) > 1)
        appended_message = self.mock_chat_request.messages[-1]
        self.assertEqual(appended_message.role, "assistant")
        self.assertIn(f"Local deployment process started for {self.repo_url}", appended_message.content)
        self.assertEqual(response_dict["status"], "success")


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy') # Mock for cleanup on failure
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script')
    @patch('app.services.orchestration_service.settings') # To control settings values like EC2_DEFAULT_KEY_NAME
    async def test_handle_cloud_local_deployment_success(
        self, mock_settings, mock_gen_bootstrap, mock_gen_tf_config,
        mock_tf_init, mock_tf_apply, mock_tf_destroy,
        mock_mkdtemp, mock_rmtree):

        # Setup mocks
        mock_settings.EC2_DEFAULT_KEY_NAME = "test-key-from-settings" # Ensure a key name is available via settings
        mock_settings.DEFAULT_KIND_VERSION = "0.20.0" # For bootstrap context
        mock_settings.DEFAULT_KUBECTL_VERSION = "1.27.0" # For bootstrap context
        mock_settings.EC2_DEFAULT_AMI_ID = "ami-settings"
        mock_settings.EC2_DEFAULT_INSTANCE_TYPE = "t2.small"
        # settings.EC2_DEFAULT_APP_PORTS is a property, ensure it returns a valid list
        mock_settings.EC2_DEFAULT_APP_PORTS = [{"port": 80, "protocol": "tcp"}]


        mock_gen_bootstrap.return_value = "#!/bin/bash\necho 'Mocked Bootstrap'"
        mock_gen_tf_config.return_value = "/mocked/temp_workspace/main.tf"
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_apply.return_value = (True, {"public_ip": "1.2.3.4", "instance_id": "i-123"}, "Apply success", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        # Test with ec2_key_name provided in request
        chat_request.ec2_key_name = "test-key-from-request"


        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success")
        self.assertIn("Cloud-local EC2 instance", response["message"])
        self.assertIn("1.2.3.4", response["message"])

        mock_mkdtemp.assert_called_once()
        mock_gen_bootstrap.assert_called_once()
        # Check that tf_context passed to generate_ec2_tf_config used the request's key_name
        tf_config_call_args = mock_gen_tf_config.call_args[0][0] # First arg is the context dict
        self.assertEqual(tf_config_call_args["key_name"], "test-key-from-request")

        mock_gen_tf_config.assert_called_once_with(unittest.mock.ANY, "/mocked/temp_workspace")
        mock_tf_init.assert_called_once_with("/mocked/temp_workspace", unittest.mock.ANY) # Check env vars if needed
        mock_tf_apply.assert_called_once_with("/mocked/temp_workspace", unittest.mock.ANY)
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace")
        mock_tf_destroy.assert_not_called()


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script', return_value=None)
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_bootstrap_fails(
        self, mock_settings, mock_gen_bootstrap, mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_DEFAULT_KEY_NAME = "test-key" # Ensure this doesn't cause failure before bootstrap

        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, self.mock_chat_request)
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate EC2 bootstrap script", response["message"])
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace")

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script', return_value="#!/bin/bash\necho 'Mocked Bootstrap'")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config', return_value=None)
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_tf_config_fails(
        self, mock_settings, mock_gen_tf_config, mock_gen_bootstrap, mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_DEFAULT_KEY_NAME = "test-key"

        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, self.mock_chat_request)
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate Terraform EC2 configuration", response["message"])
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace")

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy') # Mock for cleanup on failure
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_tf_init_fails(
        self, mock_settings, mock_gen_bootstrap, mock_gen_tf_config,
        mock_tf_init, mock_tf_apply, mock_tf_destroy,
        mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_DEFAULT_KEY_NAME = "test-key"
        mock_gen_bootstrap.return_value = "#!/bin/bash\necho 'Mocked Bootstrap'"
        mock_gen_tf_config.return_value = "/mocked/temp_workspace/main.tf"
        mock_tf_init.return_value = (False, "", "Terraform init failed")

        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, self.mock_chat_request)
        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform init failed", response["message"])
        mock_tf_apply.assert_not_called()
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace")

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_tf_apply_fails_destroy_succeeds(
        self, mock_settings, mock_gen_bootstrap, mock_gen_tf_config,
        mock_tf_init, mock_tf_apply, mock_tf_destroy,
        mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_DEFAULT_KEY_NAME = "test-key"
        mock_gen_bootstrap.return_value = "#!/bin/bash\necho 'Mocked Bootstrap'"
        mock_gen_tf_config.return_value = "/mocked/temp_workspace/main.tf"
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_apply.return_value = (False, {}, "", "Terraform apply failed")
        mock_tf_destroy.return_value = (True, "Destroy success", "")

        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, self.mock_chat_request)
        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform apply failed", response["message"])

        # Check that a message about successful destroy was appended
        self.assertTrue(any("Attempted to clean up any partially created resources" in m.content and "Destroy successful" in m.content for m in self.mock_chat_request.messages if m.role == "assistant"))
        mock_tf_destroy.assert_called_once()
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace")

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_tf_apply_fails_destroy_fails(
        self, mock_settings, mock_gen_bootstrap, mock_gen_tf_config,
        mock_tf_init, mock_tf_apply, mock_tf_destroy,
        mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_DEFAULT_KEY_NAME = "test-key"
        mock_gen_bootstrap.return_value = "#!/bin/bash\necho 'Mocked Bootstrap'"
        mock_gen_tf_config.return_value = "/mocked/temp_workspace/main.tf"
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_apply.return_value = (False, {}, "", "Terraform apply failed")
        mock_tf_destroy.return_value = (False, "", "Terraform destroy failed")

        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, self.mock_chat_request)
        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform apply failed", response["message"])
        self.assertTrue(any("Attempted to clean up" in m.content and "Destroy command failed" in m.content for m in self.mock_chat_request.messages if m.role == "assistant"))
        mock_tf_destroy.assert_called_once()
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace")

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_no_key_name(
        self, mock_settings, mock_gen_tf_config, mock_gen_bootstrap, mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_DEFAULT_KEY_NAME = None # Ensure settings has no default key name
        chat_request_no_key = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="deploy")],
            ec2_key_name=None # Explicitly no key name in request
        )

        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request_no_key)

        self.assertEqual(response["status"], "error")
        self.assertIn("EC2 Key Name is not configured", response["message"])
        mock_gen_bootstrap.assert_called_once() # Bootstrap might still be generated before key check
        mock_gen_tf_config.assert_not_called() # TF config gen should not happen if key is missing
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace") # Workspace should still be cleaned


    @patch('app.services.orchestration_service.logger')
    async def test_handle_cloud_hosted_deployment_placeholder(self, mock_logger):
        # This test is for the placeholder nature of cloud-hosted
        response_dict = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, self.mock_chat_request)

        self.assertTrue(len(self.mock_chat_request.messages) > 1)
        appended_message = self.mock_chat_request.messages[-1]
        self.assertEqual(appended_message.role, "assistant")
        self.assertIn("Cloud-hosted (EKS) deployment process started", appended_message.content)
        self.assertIn("This feature is under construction", appended_message.content)

        self.assertEqual(response_dict["status"], "pending_feature")
        self.assertEqual(response_dict["mode"], "cloud-hosted")
        self.assertEqual(response_dict["aws_region_processed"], self.aws_creds.aws_region)
        # Check that AWS creds preparation is logged
        log_found = any(f"AWS environment variables prepared for cloud-hosted deployment. Region: {self.aws_creds.aws_region}" in call_arg[0][0] for call_arg in mock_logger.info.call_args_list)
        self.assertTrue(log_found)

if __name__ == '__main__':
    unittest.main()
