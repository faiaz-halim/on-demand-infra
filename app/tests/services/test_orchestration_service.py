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

from app.core.schemas import AWSCredentials, ChatCompletionRequest, ChatMessage
from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment
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
        self.mock_chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="Deploy")])


    def tearDown(self):
        app_settings.EC2_DEFAULT_KEY_NAME = self.original_ec2_key_name
        app_settings.EC2_PRIVATE_KEY_BASE_PATH = self.original_private_key_base_path

    async def test_handle_local_deployment_no_creds_involved(self):
        # ... (test remains the same)
        response_dict = await handle_local_deployment(self.repo_url, self.namespace, self.mock_chat_request)
        self.assertTrue(len(self.mock_chat_request.messages) > 1)
        appended_message = self.mock_chat_request.messages[-1]
        self.assertEqual(appended_message.role, "assistant")
        self.assertIn(f"Local deployment process started for {self.repo_url}", appended_message.content)
        self.assertEqual(response_dict["status"], "success")


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp') # Will be called twice
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script')
    @patch('app.services.orchestration_service.ssh_service.execute_remote_command')
    @patch('app.services.orchestration_service.ssh_service.upload_file_sftp', return_value=True) # Mock SFTP upload
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

        # --- Setup Mocks ---
        mock_orch_settings.EC2_DEFAULT_KEY_NAME = "default_key.pem"
        mock_orch_settings.DEFAULT_KIND_VERSION = "0.20.0"
        mock_orch_settings.DEFAULT_KUBECTL_VERSION = "1.27.0"
        mock_orch_settings.EC2_DEFAULT_AMI_ID = "ami-settings"
        mock_orch_settings.EC2_DEFAULT_INSTANCE_TYPE = "t2.small"
        mock_orch_settings.EC2_DEFAULT_APP_PORTS = [{"port": 80, "protocol": "tcp"}] # Used to derive container_port
        mock_orch_settings.EC2_PRIVATE_KEY_BASE_PATH = "/test/keys"
        mock_orch_settings.EC2_SSH_USERNAME = "test-user"
        mock_orch_settings.EC2_DEFAULT_REPO_PATH = "/home/test-user/app"
        mock_orch_settings.KIND_CLUSTER_NAME = "mcp-kind-cluster" # For kind load command
        mock_orch_settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH = "/tmp/mcp_manifests_remote"


        mock_path_instance = mock_pathlib_Path.return_value
        mock_path_instance.exists.return_value = True

        # tempfile.mkdtemp is called twice: once for TF workspace, once for local manifests
        mock_mkdtemp.side_effect = ["/mocked/tf_workspace", "/mocked/local_manifest_temp"]

        mock_gen_bootstrap.return_value = "#!/bin/bash\necho 'Mocked Bootstrap'"
        mock_gen_tf_config.return_value = "/mocked/tf_workspace/main.tf"
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_apply.return_value = (True, {"public_ip": "1.2.3.4", "instance_id": "i-123"}, "Apply success", "")

        mock_ssh_exec.side_effect = [
            ("Cloned successfully", "", 0),                     # Git clone
            ("Image built successfully", "", 0),                # Docker build
            ("Image loaded into Kind", "", 0),                  # kind load docker-image
            ("Remote manifest dir created", "", 0),             # mkdir -p remote_manifest_dir
            ("Manifests applied to K8s", "", 0),                # kubectl apply
            ("Remote manifests cleaned up", "", 0)              # rm -rf remote_manifest_dir
        ]

        chat_request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="deploy")],
            ec2_key_name="user_provided_key.pem",
            target_namespace=self.namespace # Use self.namespace for consistency
        )

        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success")
        self.assertIn("Application 'repo' deployed to Kind on EC2", response["message"])
        self.assertTrue(response["app_url"].startswith("http://1.2.3.4:")) # Check if NodePort is in URL

        # TF calls
        tf_config_call_context = mock_gen_tf_config.call_args[0][0]
        self.assertEqual(tf_config_call_context["key_name"], "user_provided_key.pem")
        # Check that app_ports for TF context contains the calculated NodePort
        expected_node_port = 30000 + (sum(ord(c) for c in "repo") % 2768) # app_name is 'repo'
        self.assertIn({'port': expected_node_port, 'protocol': 'tcp'}, tf_config_call_context["app_ports"])


        # Manifest service calls
        mock_gen_dep_manifest.assert_called_once()
        self.assertEqual(mock_gen_dep_manifest.call_args[1]['image_name'], "repo:latest")
        self.assertEqual(mock_gen_dep_manifest.call_args[1]['app_name'], "repo")
        self.assertEqual(mock_gen_dep_manifest.call_args[1]['namespace'], self.namespace)

        mock_gen_svc_manifest.assert_called_once()
        self.assertEqual(mock_gen_svc_manifest.call_args[1]['app_name'], "repo")
        self.assertEqual(mock_gen_svc_manifest.call_args[1]['namespace'], self.namespace)
        self.assertEqual(mock_gen_svc_manifest.call_args[1]['ports_mapping'][0]['nodePort'], expected_node_port)


        # SSH calls
        self.assertEqual(mock_ssh_exec.call_count, 6)
        # ... (detailed checks for each ssh_exec call args if necessary, e.g. commands)
        clone_cmd_arg = mock_ssh_exec.call_args_list[0][0][3] # command for clone
        self.assertIn("git clone", clone_cmd_arg)
        build_cmd_arg = mock_ssh_exec.call_args_list[1][0][3] # command for build
        self.assertIn("sudo docker build -t repo:latest", build_cmd_arg)
        load_cmd_arg = mock_ssh_exec.call_args_list[2][0][3] # command for kind load
        self.assertIn("sudo kind load docker-image repo:latest --name mcp-kind-cluster", load_cmd_arg)
        mkdir_cmd_arg = mock_ssh_exec.call_args_list[3][0][3] # command for mkdir
        self.assertIn(f"mkdir -p {mock_orch_settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH}", mkdir_cmd_arg)
        apply_cmd_arg = mock_ssh_exec.call_args_list[4][0][3] # command for kubectl apply
        self.assertIn(f"sudo kubectl apply --namespace {self.namespace} -f {mock_orch_settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH}/", apply_cmd_arg)
        rm_cmd_arg = mock_ssh_exec.call_args_list[5][0][3] # command for rm
        self.assertIn(f"rm -rf {mock_orch_settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH}", rm_cmd_arg)


        # SFTP calls
        mock_upload_sftp.assert_any_call(unittest.mock.ANY, mock_orch_settings.EC2_SSH_USERNAME, unittest.mock.ANY, str(pathlib.Path("/mocked/local_manifest_temp") / "deployment.yaml"), f"{mock_orch_settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH}/deployment.yaml")
        mock_upload_sftp.assert_any_call(unittest.mock.ANY, mock_orch_settings.EC2_SSH_USERNAME, unittest.mock.ANY, str(pathlib.Path("/mocked/local_manifest_temp") / "service.yaml"), f"{mock_orch_settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH}/service.yaml")
        self.assertEqual(mock_upload_sftp.call_count, 2)

        # Workspace and temp dir cleanup
        mock_rmtree.assert_any_call("/mocked/tf_workspace")
        mock_rmtree.assert_any_call("/mocked/local_manifest_temp")
        self.assertEqual(mock_rmtree.call_count, 2)

        mock_tf_destroy.assert_not_called()

    # ... (existing failure tests for bootstrap, tf_config, tf_init, tf_apply, key issues remain valuable) ...
    # Add new failure tests for Kind deployment part

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply', return_value=(True, {"public_ip": "1.2.3.4"}, "", ""))
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config', return_value="/mocked_tf_file.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script', return_value="mock_bootstrap")
    @patch('app.services.orchestration_service.ssh_service.execute_remote_command')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_kind_load_fails(
        self, mock_settings, mock_pathlib_Path, mock_ssh_exec,
        mock_gen_bootstrap, mock_gen_tf_config, mock_tf_init, mock_tf_apply,
        mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_PRIVATE_KEY_BASE_PATH = "/test/keys"
        mock_settings.EC2_DEFAULT_KEY_NAME = "default_key.pem"
        mock_path_instance = mock_pathlib_Path.return_value
        mock_path_instance.exists.return_value = True

        mock_ssh_exec.side_effect = [
            ("Cloned", "", 0), # Git clone
            ("Built", "", 0),  # Docker build
            ("", "Kind load failed", 1) # kind load docker-image fails
        ]
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], ec2_key_name="default_key.pem")
        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to load image into Kind on EC2", response["message"])
        self.assertEqual(mock_ssh_exec.call_count, 3) # Clone, Build, Kind Load attempt
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace")

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply', return_value=(True, {"public_ip": "1.2.3.4"}, "", ""))
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config', return_value="/mocked_tf_file.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script', return_value="mock_bootstrap")
    @patch('app.services.orchestration_service.ssh_service.execute_remote_command', side_effect=[("", "", 0), ("", "", 0), ("", "", 0)]) # Clone, Build, Kind Load succeed
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value=None) # Manifest gen fails
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="kind: Service...") # Assume service gen ok for this test
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_manifest_gen_fails(
        self, mock_settings, mock_pathlib_Path, mock_gen_svc, mock_gen_dep, mock_ssh_exec,
        mock_gen_bootstrap, mock_gen_tf_config, mock_tf_init, mock_tf_apply,
        mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_PRIVATE_KEY_BASE_PATH = "/test/keys"
        mock_settings.EC2_DEFAULT_KEY_NAME = "default_key.pem"
        mock_path_instance = mock_pathlib_Path.return_value
        mock_path_instance.exists.return_value = True

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], ec2_key_name="default_key.pem")
        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate Kubernetes manifests locally", response["message"])
        mock_gen_dep.assert_called_once() # generate_deployment_manifest was called
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace")


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp') # Called for TF workspace and local manifests
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply', return_value=(True, {"public_ip": "1.2.3.4"}, "", ""))
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config', return_value="/mocked_tf_file.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script', return_value="mock_bootstrap")
    @patch('app.services.orchestration_service.ssh_service.execute_remote_command')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="kind: Deployment...")
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="kind: Service...")
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_mkdir_remote_fails(
        self, mock_settings, mock_pathlib_Path, mock_gen_svc, mock_gen_dep, mock_ssh_exec,
        mock_gen_bootstrap, mock_gen_tf_config, mock_tf_init, mock_tf_apply,
        mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_PRIVATE_KEY_BASE_PATH = "/test/keys"
        mock_settings.EC2_DEFAULT_KEY_NAME = "default_key.pem"
        mock_path_instance = mock_pathlib_Path.return_value
        mock_path_instance.exists.return_value = True
        mock_mkdtemp.side_effect = ["/mocked/tf_workspace", "/mocked/local_manifest_temp"]

        mock_ssh_exec.side_effect = [
            ("", "", 0),  # Git clone
            ("", "", 0),  # Docker build
            ("", "", 0),  # kind load docker-image
            ("", "mkdir failed", 1) # mkdir -p remote_manifest_dir Fails
        ]
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], ec2_key_name="default_key.pem")
        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to create remote manifest directory", response["message"])
        # Ensure rmtree is called for local_manifest_temp_dir if it was created
        mock_rmtree.assert_any_call("/mocked/local_manifest_temp")
        mock_rmtree.assert_any_call("/mocked/tf_workspace")


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply', return_value=(True, {"public_ip": "1.2.3.4"}, "", ""))
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config', return_value="/mocked_tf_file.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script', return_value="mock_bootstrap")
    @patch('app.services.orchestration_service.ssh_service.execute_remote_command')
    @patch('app.services.orchestration_service.ssh_service.upload_file_sftp', return_value=False) # SFTP fails
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="kind: Deployment...")
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="kind: Service...")
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_sftp_upload_fails(
        self, mock_settings, mock_pathlib_Path, mock_gen_svc, mock_gen_dep, mock_upload_sftp, mock_ssh_exec,
        mock_gen_bootstrap, mock_gen_tf_config, mock_tf_init, mock_tf_apply,
        mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_PRIVATE_KEY_BASE_PATH = "/test/keys"
        mock_settings.EC2_DEFAULT_KEY_NAME = "default_key.pem"
        mock_path_instance = mock_pathlib_Path.return_value
        mock_path_instance.exists.return_value = True
        mock_mkdtemp.side_effect = ["/mocked/tf_workspace", "/mocked/local_manifest_temp"]

        mock_ssh_exec.side_effect = [ # All SSH exec commands prior to SFTP succeed
            ("", "", 0), ("","",0), ("","",0), ("","",0)
        ]
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], ec2_key_name="default_key.pem")
        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to upload manifests to EC2", response["message"])
        mock_upload_sftp.assert_called() # Assert it was attempted
        mock_rmtree.assert_any_call("/mocked/local_manifest_temp")
        mock_rmtree.assert_any_call("/mocked/tf_workspace")

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply', return_value=(True, {"public_ip": "1.2.3.4"}, "", ""))
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config', return_value="/mocked_tf_file.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script', return_value="mock_bootstrap")
    @patch('app.services.orchestration_service.ssh_service.execute_remote_command')
    @patch('app.services.orchestration_service.ssh_service.upload_file_sftp', return_value=True)
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="kind: Deployment...")
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="kind: Service...")
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_kubectl_apply_fails(
        self, mock_settings, mock_pathlib_Path, mock_gen_svc, mock_gen_dep, mock_upload_sftp, mock_ssh_exec,
        mock_gen_bootstrap, mock_gen_tf_config, mock_tf_init, mock_tf_apply,
        mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_PRIVATE_KEY_BASE_PATH = "/test/keys"
        mock_settings.EC2_DEFAULT_KEY_NAME = "default_key.pem"
        mock_path_instance = mock_pathlib_Path.return_value
        mock_path_instance.exists.return_value = True
        mock_mkdtemp.side_effect = ["/mocked/tf_workspace", "/mocked/local_manifest_temp"]

        mock_ssh_exec.side_effect = [
            ("", "", 0),  # Git clone
            ("", "", 0),  # Docker build
            ("", "", 0),  # kind load docker-image
            ("", "", 0),  # mkdir -p remote_manifest_dir
            ("", "kubectl apply failed", 1), # kubectl apply Fails
            ("", "", 0) # Remote manifest cleanup (should still be attempted)
        ]
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], ec2_key_name="default_key.pem")
        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to apply K8s manifests on EC2", response["message"])
        self.assertEqual(mock_ssh_exec.call_count, 6) # All SSH commands including cleanup attempt
        mock_rmtree.assert_any_call("/mocked/local_manifest_temp")
        mock_rmtree.assert_any_call("/mocked/tf_workspace")


    # ... (Other existing tests: no_key_name, placeholder cloud_hosted, tf failures etc.) ...
    # Ensure they are not removed and still pass or are adapted if necessary.
    # The no_key_name test from previous step is still relevant.
    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mocked/temp_workspace")
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script') # Not strictly needed if key check is first
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_no_key_name( # From previous step, ensure it's still valid
        self, mock_settings, mock_gen_tf_config, mock_gen_bootstrap, mock_mkdtemp, mock_rmtree):

        mock_settings.EC2_DEFAULT_KEY_NAME = None
        chat_request_no_key = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="deploy")],
            ec2_key_name=None
        )
        response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request_no_key)

        self.assertEqual(response["status"], "error")
        self.assertIn("EC2 Key Name is not configured", response["message"])
        # Bootstrap gen might not be called if key check is very early, adjust if orchestrator logic changes.
        # Based on current orchestrator, key check is before bootstrap gen.
        mock_gen_bootstrap.assert_not_called()
        mock_gen_tf_config.assert_not_called()
        mock_rmtree.assert_called_once_with("/mocked/temp_workspace") # Workspace cleanup is important

    # Test for the placeholder cloud-hosted deployment (already present)
    @patch('app.services.orchestration_service.logger')
    async def test_handle_cloud_hosted_deployment_placeholder(self, mock_orch_logger):
        # ... (This test remains the same as before) ...
        mock_chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="Deploy cloud-hosted")])
        response_dict = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, mock_chat_request)

        self.assertTrue(len(mock_chat_request.messages) > 1)
        appended_message = mock_chat_request.messages[-1]
        self.assertEqual(appended_message.role, "assistant")
        self.assertIn("Cloud-hosted (EKS) deployment process started", appended_message.content)
        self.assertIn("This feature is under construction", appended_message.content)

        self.assertEqual(response_dict["status"], "pending_feature")
        self.assertEqual(response_dict["aws_region_processed"], self.aws_creds.aws_region)
        log_found = any(
            f"AWS environment variables prepared for cloud-hosted deployment. Region: {self.aws_creds.aws_region}" in call_arg[0][0]
            for call_arg in mock_orch_logger.info.call_args_list
        )
        self.assertTrue(log_found, "Log message for AWS env var prep in cloud-hosted not found.")


if __name__ == '__main__':
    unittest.main()
