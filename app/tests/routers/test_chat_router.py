import unittest
from unittest.mock import patch, AsyncMock # Use AsyncMock for async functions
from fastapi.testclient import TestClient
from fastapi import HTTPException # For type checking, not for raising in tests directly

from app.main import app # Import the FastAPI application instance
from app.core.schemas import ChatCompletionRequest, AWSCredentials, ChatMessage

# For mocking the Azure client if testing the passthrough chat functionality
# from openai import AzureOpenAI

class TestChatRouter(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        # Basic payload structure, customize in each test
        self.base_payload = {
            "messages": [{"role": "user", "content": "Hello"}],
            "model": "test-model", # Optional, but good to include
            # github_repo_url, deployment_mode, aws_credentials, target_namespace added per test
        }

    @patch('app.services.orchestration_service.handle_local_deployment', new_callable=AsyncMock)
    def test_deployment_local_mode_success(self, mock_handle_local):
        mock_response_data = {"status": "local deployment initiated", "details": "Processed by mock"}
        mock_handle_local.return_value = mock_response_data

        payload = {
            **self.base_payload,
            "github_repo_url": "https://github.com/user/repo.git",
            "deployment_mode": "local",
            "target_namespace": "test-local-ns"
        }

        response = self.client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_local.assert_called_once()
        # Check args (request object is passed, so it's a bit more complex to assert precisely without deep inspection)
        self.assertEqual(mock_handle_local.call_args[0][0], payload["github_repo_url"])
        self.assertEqual(mock_handle_local.call_args[0][1], payload["target_namespace"])
        self.assertIsInstance(mock_handle_local.call_args[0][2], ChatCompletionRequest)


    @patch('app.services.orchestration_service.handle_cloud_local_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_local_mode_success(self, mock_handle_cloud_local):
        mock_response_data = {"status": "cloud-local deployment initiated"}
        mock_handle_cloud_local.return_value = mock_response_data

        aws_creds_payload = {
            "aws_access_key_id": "test_key_id",
            "aws_secret_access_key": "test_secret_key",
            "aws_region": "us-west-2"
        }
        payload = {
            **self.base_payload,
            "github_repo_url": "https://github.com/user/cl-repo.git",
            "deployment_mode": "cloud-local",
            "target_namespace": "test-cl-ns",
            "aws_credentials": aws_creds_payload
        }

        response = self.client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_cloud_local.assert_called_once()
        self.assertEqual(mock_handle_cloud_local.call_args[0][0], payload["github_repo_url"])
        self.assertEqual(mock_handle_cloud_local.call_args[0][1], payload["target_namespace"])
        self.assertIsInstance(mock_handle_cloud_local.call_args[0][2], AWSCredentials) # Verifying AWSCredentials object passed
        self.assertEqual(mock_handle_cloud_local.call_args[0][2].aws_region, aws_creds_payload["aws_region"])


    @patch('app.services.orchestration_service.handle_cloud_local_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_local_mode_missing_creds(self, mock_handle_cloud_local):
        payload = {
            **self.base_payload,
            "github_repo_url": "https://github.com/user/cl-repo.git",
            "deployment_mode": "cloud-local",
            "target_namespace": "test-cl-ns",
            # aws_credentials deliberately omitted
        }

        response = self.client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("AWS credentials required", response.json()["detail"])
        mock_handle_cloud_local.assert_not_called()

    @patch('app.services.orchestration_service.handle_cloud_hosted_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_hosted_mode_success(self, mock_handle_cloud_hosted):
        mock_response_data = {"status": "cloud-hosted deployment initiated"}
        mock_handle_cloud_hosted.return_value = mock_response_data

        aws_creds_payload = {
            "aws_access_key_id": "test_key_id_ch",
            "aws_secret_access_key": "test_secret_key_ch",
            "aws_region": "eu-central-1"
        }
        payload = {
            **self.base_payload,
            "github_repo_url": "https://github.com/user/ch-repo.git",
            "deployment_mode": "cloud-hosted",
            "target_namespace": "test-ch-ns",
            "aws_credentials": aws_creds_payload
        }

        response = self.client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_cloud_hosted.assert_called_once()

    @patch('app.services.orchestration_service.handle_cloud_hosted_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_hosted_mode_missing_creds(self, mock_handle_cloud_hosted):
        payload = {
            **self.base_payload,
            "github_repo_url": "https://github.com/user/ch-repo.git",
            "deployment_mode": "cloud-hosted",
            "target_namespace": "test-ch-ns",
            # aws_credentials deliberately omitted
        }

        response = self.client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("AWS credentials required", response.json()["detail"])
        mock_handle_cloud_hosted.assert_not_called()

    @patch('app.services.orchestration_service.handle_local_deployment', new_callable=AsyncMock)
    @patch('app.services.orchestration_service.handle_cloud_local_deployment', new_callable=AsyncMock)
    @patch('app.services.orchestration_service.handle_cloud_hosted_deployment', new_callable=AsyncMock)
    def test_deployment_invalid_mode(self, mock_handle_ch, mock_handle_cl, mock_handle_local):
        payload = {
            **self.base_payload,
            "github_repo_url": "https://github.com/user/repo.git",
            "deployment_mode": "invalid_mode", # This mode does not exist
            "target_namespace": "test-ns"
        }

        response = self.client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid deployment mode", response.json()["detail"])
        mock_handle_local.assert_not_called()
        mock_handle_cl.assert_not_called()
        mock_handle_ch.assert_not_called()

    # This test depends on the actual AzureOpenAI client logic.
    # It might need more specific mocking if the client is not configured in the test environment.
    @patch('app.routers.chat.client', new_callable=MagicMock) # Mock the AzureOpenAI client instance
    @patch('app.services.orchestration_service.handle_local_deployment', new_callable=AsyncMock)
    @patch('app.services.orchestration_service.handle_cloud_local_deployment', new_callable=AsyncMock)
    @patch('app.services.orchestration_service.handle_cloud_hosted_deployment', new_callable=AsyncMock)
    def test_standard_chat_flow_no_repo_url(self, mock_handle_ch, mock_handle_cl, mock_handle_local, mock_azure_client):
        # Setup mock for non-streaming Azure client response
        mock_completion_choice = MagicMock()
        mock_completion_choice.index = 0
        mock_completion_choice.message = MagicMock(role="assistant", content="Azure OpenAI says hello")
        mock_completion_choice.finish_reason = "stop"

        mock_azure_response = MagicMock()
        mock_azure_response.id = "chatcmpl-mockid"
        mock_azure_response.created = 1234567890
        mock_azure_response.model = "gpt-mock"
        mock_azure_response.choices = [mock_completion_choice]
        mock_azure_response.usage = MagicMock(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        # Ensure the client is not None and the create method is an AsyncMock or MagicMock
        # If client is None initially, this mock won't be effective unless client is globally patched.
        # The current setup patches the 'client' instance within 'app.routers.chat' module.
        if mock_azure_client: # If client was successfully patched
             mock_azure_client.chat.completions.create = MagicMock(return_value=mock_azure_response)
        else: # If client is None in app.routers.chat (e.g. no API keys in settings during test)
            # This case means the router would raise 500 before calling orchestrators or Azure.
            # To test this path properly, we'd need to control settings.
            pass


        payload = {**self.base_payload} # No github_repo_url

        # If Azure client is not configured (e.g. missing settings), this will raise 500.
        # For this test, we assume it's configured or mocked to avoid that specific 500.
        # If settings.AZURE_OPENAI_DEPLOYMENT is not set, it will also fail.
        # Let's assume these are set for this test to focus on the routing.
        with patch('app.routers.chat.settings.AZURE_OPENAI_DEPLOYMENT', "mock_deployment"):
            if not mock_azure_client: # If client is None from the start due to settings
                 with self.assertRaises(HTTPException) as cm:
                     self.client.post("/v1/chat/completions", json=payload)
                 self.assertEqual(cm.exception.status_code, 500) # Should fail due to no client
                 self.assertIn("Azure OpenAI client is not configured", cm.exception.detail)
                 return # End test here as this is the expected outcome

            response = self.client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(response_data["choices"][0]["message"]["content"], "Azure OpenAI says hello")

        mock_handle_local.assert_not_called()
        mock_handle_cl.assert_not_called()
        mock_handle_ch.assert_not_called()
        if mock_azure_client:
            mock_azure_client.chat.completions.create.assert_called_once()


if __name__ == '__main__':
    unittest.main()
