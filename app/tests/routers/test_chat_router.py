import unittest
from unittest.mock import patch, AsyncMock, MagicMock, call
import subprocess
import os
import tempfile
import shutil
import pathlib
from pydantic import SecretStr
import uuid
import json # Added for tool call argument/result stringification
import time # Added for tool call timestamp consistency

from app.core.schemas import ChatCompletionRequest, AWSCredentials, ChatMessage, ChatCompletionResponse, Choice, Usage
from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment,
    handle_cloud_local_decommission,
    handle_cloud_hosted_decommission, # Added import
    handle_cloud_local_redeploy
)
from app.main import app
from app.core.config import settings as app_settings
from fastapi.testclient import TestClient # Added import

from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall, Function

import logging

logger = logging.getLogger('app.services.orchestration_service')

class TestChatRouter(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.base_payload = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "test-model",
        }
        self.original_ec2_key_name = app_settings.EC2_DEFAULT_KEY_NAME
        self.original_private_key_base_path = app_settings.EC2_PRIVATE_KEY_BASE_PATH

    def tearDown(self):
        app_settings.EC2_DEFAULT_KEY_NAME = self.original_ec2_key_name
        app_settings.EC2_PRIVATE_KEY_BASE_PATH = self.original_private_key_base_path

    # --- Existing Deployment Tests ---
    @patch('app.services.orchestration_service.handle_local_deployment', new_callable=AsyncMock)
    def test_deployment_local_mode_success(self, mock_handle_local):
        # ... (as before)
        mock_response_data = {"status": "local deployment initiated"}
        mock_handle_local.return_value = mock_response_data
        payload = {**self.base_payload, "action": "deploy", "github_repo_url": "https://github.com/user/repo.git", "deployment_mode": "local", "target_namespace": "test-local-ns"}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_local.assert_called_once()

    # ... (other existing deployment tests for cloud-local, cloud-hosted, invalid mode, missing creds) ...
    @patch('app.services.orchestration_service.handle_cloud_local_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_local_mode_success(self, mock_handle_cloud_local):
        mock_response_data = {"status": "cloud-local deployment initiated"}
        mock_handle_cloud_local.return_value = mock_response_data
        aws_creds_payload = {"aws_access_key_id": "test_key_id", "aws_secret_access_key": "test_secret_key", "aws_region": "us-west-2"}
        payload = {**self.base_payload, "action": "deploy", "github_repo_url": "https://github.com/user/cl-repo.git", "deployment_mode": "cloud-local", "target_namespace": "test-cl-ns", "aws_credentials": aws_creds_payload}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)

    @patch('app.services.orchestration_service.handle_cloud_local_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_local_mode_missing_creds(self, mock_handle_cloud_local):
        payload = {**self.base_payload, "action": "deploy", "github_repo_url": "https://github.com/user/cl-repo.git", "deployment_mode": "cloud-local", "target_namespace": "test-cl-ns"}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 400)

    @patch('app.services.orchestration_service.handle_cloud_hosted_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_hosted_mode_success(self, mock_handle_cloud_hosted):
        mock_response_data = {"status": "cloud-hosted deployment initiated"}
        mock_handle_cloud_hosted.return_value = mock_response_data
        aws_creds_payload = {"aws_access_key_id": "test_key_id_ch", "aws_secret_access_key": "test_secret_key_ch", "aws_region": "eu-central-1"}
        payload = {**self.base_payload, "action": "deploy", "github_repo_url": "https://github.com/user/ch-repo.git", "deployment_mode": "cloud-hosted", "target_namespace": "test-ch-ns", "aws_credentials": aws_creds_payload}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)

    @patch('app.services.orchestration_service.handle_cloud_hosted_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_hosted_mode_missing_creds(self, mock_handle_cloud_hosted):
        payload = {**self.base_payload, "action": "deploy", "github_repo_url": "https://github.com/user/ch-repo.git", "deployment_mode": "cloud-hosted", "target_namespace": "test-ch-ns"}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 400)

    @patch('app.services.orchestration_service.handle_local_deployment', new_callable=AsyncMock)
    def test_deployment_invalid_mode(self, mock_handle_local): # Simplified as other mocks aren't needed here
        payload = {**self.base_payload, "action": "deploy", "github_repo_url": "https://github.com/user/repo.git", "deployment_mode": "invalid_mode", "target_namespace": "test-ns"}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid deployment mode", response.json()["detail"])

    # --- Decommission Tests (from previous subtask, ensure they still work with action dispatch) ---
    @patch('app.routers.chat.handle_cloud_local_decommission', new_callable=AsyncMock)
    def test_decommission_action_cloud_local_success(self, mock_handle_decommission):
        mock_response_data = {"status": "decommission_success", "instance_id": "i-123"}
        mock_handle_decommission.return_value = mock_response_data
        aws_creds_payload = {"aws_access_key_id": "key", "aws_secret_access_key": "secret", "aws_region": "us-east-1"}
        payload = {**self.base_payload, "action": "decommission", "deployment_mode": "cloud-local", "instance_id": "i-123", "aws_credentials": aws_creds_payload}

        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_decommission.assert_called_once()

    # --- New Redeploy Tests ---
    @patch('app.routers.chat.handle_cloud_local_redeploy', new_callable=AsyncMock)
    def test_redeploy_action_cloud_local_success(self, mock_handle_redeploy):
        mock_response_data = {"status": "redeploy_success", "instance_id": "i-123", "app_url": "http://1.2.3.4:30080"}
        mock_handle_redeploy.return_value = mock_response_data

        aws_creds_payload = {"aws_access_key_id": "test_key", "aws_secret_access_key": "test_secret", "aws_region": "us-west-1"}
        payload = {
            **self.base_payload,
            "action": "redeploy",
            "deployment_mode": "cloud-local",
            "instance_id": "i-123",
            "public_ip": "1.2.3.4",
            "ec2_key_name": "test-key.pem",
            "github_repo_url": "https://github.com/user/new-version-repo.git", # Could be same or different
            "target_namespace": "prod",
            "aws_credentials": aws_creds_payload # Optional for redeploy in orchestrator, but API might enforce for cloud modes
        }
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_redeploy.assert_called_once_with(
            instance_id="i-123",
            public_ip="1.2.3.4",
            ec2_key_name="test-key.pem",
            repo_url="https://github.com/user/new-version-repo.git",
            namespace="prod",
            aws_creds=unittest.mock.ANY, # AWSCredentials object
            chat_request=unittest.mock.ANY # ChatCompletionRequest object
        )

    def test_redeploy_action_missing_parameters(self):
        base_redeploy_payload = {
            **self.base_payload,
            "action": "redeploy",
            "deployment_mode": "cloud-local",
            "instance_id": "i-123",
            "public_ip": "1.2.3.4",
            "ec2_key_name": "test-key.pem",
            "github_repo_url": "https://github.com/user/new-version-repo.git",
            "target_namespace": "prod",
        }

        required_fields = ["instance_id", "public_ip", "ec2_key_name", "github_repo_url", "target_namespace"]
        for field_to_omit in required_fields:
            with self.subTest(missing_field=field_to_omit):
                payload = base_redeploy_payload.copy()
                del payload[field_to_omit]
                response = self.client.post("/v1/chat/completions", json=payload)
                self.assertEqual(response.status_code, 400, f"Failed for missing field: {field_to_omit}")
                self.assertIn(field_to_omit, response.json()["detail"].lower(), f"Error detail for missing {field_to_omit} not as expected.")

    def test_redeploy_action_unsupported_mode(self):
        payload = {
            **self.base_payload,
            "action": "redeploy",
            "deployment_mode": "local", # Not yet supported for redeploy action
            "instance_id": "i-123",
            "public_ip": "1.2.3.4",
            "ec2_key_name": "test-key.pem",
            "github_repo_url": "https://github.com/user/new-version-repo.git",
            "target_namespace": "prod",
        }
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 501) # Or 400 depending on router implementation for this case
        self.assertIn("Redeploy action for mode 'local' is not yet implemented", response.json()["detail"])


    # --- Tool Call Tests (from previous subtask, ensure they still work) ---
    @patch('app.routers.chat.execute_tool', new_callable=AsyncMock)
    @patch('app.routers.chat.TOOL_DEFINITIONS', [{"type": "function", "function": {"name": "sample_tool", "parameters": {}}}])
    @patch('app.routers.chat.client')
    def test_chat_completion_with_tool_call_success(self, mock_azure_client, mock_tool_definitions_val, mock_execute_tool):
        # ... (as before)
        mock_tool_call = ChatCompletionMessageToolCall(id="call123", function=Function(name="sample_tool", arguments='{"query": "test"}'), type="function")
        first_llm_response_message = MagicMock(role="assistant", content=None, tool_calls=[mock_tool_call])
        first_llm_completion = MagicMock(choices=[MagicMock(message=first_llm_response_message)], usage=MagicMock(), id="c1", created=1, model="m1")
        mock_execute_tool.return_value = {"status": "success", "result": {"info": "tool result from mock"}}
        second_llm_response_message = MagicMock(role="assistant", content="Final answer after tool call.", tool_calls=None)
        second_llm_completion = MagicMock(choices=[MagicMock(message=second_llm_response_message)], usage=MagicMock(), id="c2", created=2, model="m1")
        mock_azure_client.chat.completions.create.side_effect = [first_llm_completion, second_llm_completion]
        payload = {**self.base_payload, "stream": False}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "Final answer after tool call.")


    # ... (other existing tests: tool_exec_fails, no_tool_call, streaming_bypasses_tools, standard_chat_flow)
    @patch('app.routers.chat.client', new_callable=MagicMock)
    @patch('app.services.orchestration_service.handle_local_deployment', new_callable=AsyncMock)
    def test_standard_chat_flow_no_repo_url_no_action(self, mock_handle_local, mock_azure_client): # Renamed for clarity
        mock_completion_choice = MagicMock(index=0, message=MagicMock(role="assistant", content="Azure says hello"), finish_reason="stop")
        mock_azure_response = MagicMock(id="cmpl-1", created=123, model="gpt", choices=[mock_completion_choice], usage=MagicMock(prompt_tokens=1,completion_tokens=1,total_tokens=2))
        mock_azure_client.chat.completions.create.return_value = mock_azure_response

        payload = {**self.base_payload, "action": "deploy"} # No github_repo_url, action is deploy (default)
        # This should fall through to standard chat if github_repo_url is None, despite action being "deploy"
        # Or, it might be an error depending on router strictness for "deploy" action.
        # Current router logic: if action=="deploy", github_repo_url is mandatory.
        # So this test needs to be adjusted or the payload should not have action="deploy" if no repo.
        # Let's assume it's just a general chat, so action should not be "deploy" or it should not have github_repo_url.
        # If no specific action that requires specific params is hit, it's a chat.

        # Test with no specific "action" that would be handled by deployment/lifecycle logic
        chat_payload = {**self.base_payload}
        del chat_payload['action'] # Let action default in Pydantic model, which is 'deploy', but since no repo_url, it will be chat

        with patch('app.routers.chat.settings.AZURE_OPENAI_DEPLOYMENT', "mock_deployment"):
            response = self.client.post("/v1/chat/completions", json=chat_payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "Azure says hello")
        mock_handle_local.assert_not_called()
        mock_azure_client.chat.completions.create.assert_called_once()

    # --- New Decommission Cloud-Hosted Tests ---
    @patch('app.routers.chat.handle_cloud_hosted_decommission', new_callable=AsyncMock)
    def test_decommission_action_cloud_hosted_success(self, mock_handle_ch_decommission):
        mock_response_data = {"status": "cloud_hosted_decommission_success", "instance_id": "my-eks-cluster"}
        mock_handle_ch_decommission.return_value = mock_response_data
        aws_creds_payload = {"aws_access_key_id": "key_ch", "aws_secret_access_key": "secret_ch", "aws_region": "eu-central-1"}
        payload = {
            **self.base_payload,
            "action": "decommission",
            "deployment_mode": "cloud-hosted",
            "instance_id": "my-eks-cluster",
            "aws_credentials": aws_creds_payload
        }

        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_ch_decommission.assert_called_once()
        # Check specific args passed to the handler
        call_args = mock_handle_ch_decommission.call_args[0]
        self.assertEqual(call_args[0], "my-eks-cluster") # cluster_name
        self.assertIsInstance(call_args[1], AWSCredentials) # aws_creds
        self.assertIsInstance(call_args[2], ChatCompletionRequest) # chat_request

    def test_decommission_action_cloud_hosted_missing_instance_id(self):
        aws_creds_payload = {"aws_access_key_id": "key_ch", "aws_secret_access_key": "secret_ch", "aws_region": "eu-central-1"}
        payload = {
            **self.base_payload,
            "action": "decommission",
            "deployment_mode": "cloud-hosted",
            # "instance_id": "my-eks-cluster", # Missing
            "aws_credentials": aws_creds_payload
        }
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Instance ID ('instance_id') is required for 'decommission' action.", response.json()["detail"])

    def test_decommission_action_cloud_hosted_missing_aws_creds(self):
        payload = {
            **self.base_payload,
            "action": "decommission",
            "deployment_mode": "cloud-hosted",
            "instance_id": "my-eks-cluster"
            # "aws_credentials": aws_creds_payload # Missing
        }
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("AWS credentials are required for cloud-hosted decommission action.", response.json()["detail"])


if __name__ == '__main__':
    unittest.main()
