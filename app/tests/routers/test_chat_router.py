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

from app.core.schemas import ChatCompletionRequest, AWSCredentials, ChatMessage, ChatCompletionResponse, Choice, Usage
from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment
)
from app.main import app
from app.core.config import settings as app_settings # For direct access if needed in tests, though patching is preferred

# For mocking OpenAI client library tool call structures
from openai.types.chat.chat_completion_message_tool_call import ChatCompletionMessageToolCall, Function

import logging

logger = logging.getLogger('app.services.orchestration_service')

class TestChatRouter(unittest.TestCase): # Changed to unittest.TestCase for non-async setup/teardown

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

    # ... (existing deployment tests remain here) ...
    @patch('app.services.orchestration_service.handle_local_deployment', new_callable=AsyncMock)
    def test_deployment_local_mode_success(self, mock_handle_local):
        mock_response_data = {"status": "local deployment initiated", "details": "Processed by mock"}
        mock_handle_local.return_value = mock_response_data
        payload = {**self.base_payload, "github_repo_url": "https://github.com/user/repo.git", "deployment_mode": "local", "target_namespace": "test-local-ns"}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_local.assert_called_once()

    @patch('app.services.orchestration_service.handle_cloud_local_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_local_mode_success(self, mock_handle_cloud_local):
        mock_response_data = {"status": "cloud-local deployment initiated"}
        mock_handle_cloud_local.return_value = mock_response_data
        aws_creds_payload = {"aws_access_key_id": "test_key_id", "aws_secret_access_key": "test_secret_key", "aws_region": "us-west-2"}
        payload = {**self.base_payload, "github_repo_url": "https://github.com/user/cl-repo.git", "deployment_mode": "cloud-local", "target_namespace": "test-cl-ns", "aws_credentials": aws_creds_payload}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_cloud_local.assert_called_once()

    @patch('app.services.orchestration_service.handle_cloud_local_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_local_mode_missing_creds(self, mock_handle_cloud_local):
        payload = {**self.base_payload, "github_repo_url": "https://github.com/user/cl-repo.git", "deployment_mode": "cloud-local", "target_namespace": "test-cl-ns"}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("AWS credentials required", response.json()["detail"])
        mock_handle_cloud_local.assert_not_called()

    @patch('app.services.orchestration_service.handle_cloud_hosted_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_hosted_mode_success(self, mock_handle_cloud_hosted):
        mock_response_data = {"status": "cloud-hosted deployment initiated"}
        mock_handle_cloud_hosted.return_value = mock_response_data
        aws_creds_payload = {"aws_access_key_id": "test_key_id_ch", "aws_secret_access_key": "test_secret_key_ch", "aws_region": "eu-central-1"}
        payload = {**self.base_payload, "github_repo_url": "https://github.com/user/ch-repo.git", "deployment_mode": "cloud-hosted", "target_namespace": "test-ch-ns", "aws_credentials": aws_creds_payload}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), mock_response_data)
        mock_handle_cloud_hosted.assert_called_once()

    @patch('app.services.orchestration_service.handle_cloud_hosted_deployment', new_callable=AsyncMock)
    def test_deployment_cloud_hosted_mode_missing_creds(self, mock_handle_cloud_hosted):
        payload = {**self.base_payload, "github_repo_url": "https://github.com/user/ch-repo.git", "deployment_mode": "cloud-hosted", "target_namespace": "test-ch-ns"}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("AWS credentials required", response.json()["detail"])
        mock_handle_cloud_hosted.assert_not_called()

    @patch('app.services.orchestration_service.handle_local_deployment', new_callable=AsyncMock)
    @patch('app.services.orchestration_service.handle_cloud_local_deployment', new_callable=AsyncMock)
    @patch('app.services.orchestration_service.handle_cloud_hosted_deployment', new_callable=AsyncMock)
    def test_deployment_invalid_mode(self, mock_handle_ch, mock_handle_cl, mock_handle_local):
        payload = {**self.base_payload, "github_repo_url": "https://github.com/user/repo.git", "deployment_mode": "invalid_mode", "target_namespace": "test-ns"}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid deployment mode", response.json()["detail"])

    # Tool Call Tests
    @patch('app.routers.chat.execute_tool', new_callable=AsyncMock)
    @patch('app.routers.chat.TOOL_DEFINITIONS', [{"type": "function", "function": {"name": "sample_tool", "parameters": {}}}])
    @patch('app.routers.chat.client') # Mock the AzureOpenAI client instance in chat.py
    def test_chat_completion_with_tool_call_success(self, mock_azure_client, mock_tool_definitions_val, mock_execute_tool):
        # First LLM response: requests a tool call
        mock_tool_call = ChatCompletionMessageToolCall(
            id="call123",
            function=Function(name="sample_tool", arguments='{"query": "test"}'),
            type="function"
        )
        first_llm_response_message = MagicMock()
        first_llm_response_message.role = "assistant"
        first_llm_response_message.content = None # Important: content is None when tool_calls are present
        first_llm_response_message.tool_calls = [mock_tool_call]

        first_llm_completion = MagicMock()
        first_llm_completion.choices = [MagicMock(message=first_llm_response_message, finish_reason="tool_calls")]
        first_llm_completion.usage = MagicMock(prompt_tokens=10, completion_tokens=10, total_tokens=20)
        first_llm_completion.id = "first_call_id"
        first_llm_completion.created = int(time.time())
        first_llm_completion.model = "test_model_for_tools"

        # Tool execution result
        mock_execute_tool.return_value = {"status": "success", "result": {"info": "tool result from mock"}}

        # Second LLM response: final user-facing message
        second_llm_response_message = MagicMock(role="assistant", content="Final answer after tool call.", tool_calls=None)
        second_llm_completion = MagicMock()
        second_llm_completion.choices = [MagicMock(message=second_llm_response_message, finish_reason="stop")]
        second_llm_completion.usage = MagicMock(prompt_tokens=30, completion_tokens=15, total_tokens=45) # Example usage
        second_llm_completion.id = "second_call_id"
        second_llm_completion.created = int(time.time())
        second_llm_completion.model = "test_model_for_tools"

        mock_azure_client.chat.completions.create.side_effect = [first_llm_completion, second_llm_completion]

        payload = {**self.base_payload, "stream": False} # Ensure non-streaming
        response = self.client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 200)
        response_data = response.json()
        self.assertEqual(response_data["choices"][0]["message"]["content"], "Final answer after tool call.")

        self.assertEqual(mock_azure_client.chat.completions.create.call_count, 2)
        # First call checks
        first_call_args = mock_azure_client.chat.completions.create.call_args_list[0][1] # kwargs of first call
        self.assertEqual(first_call_args['tools'], mock_tool_definitions_val) # Check if TOOL_DEFINITIONS was passed
        self.assertEqual(first_call_args['tool_choice'], "auto")

        # Check call to execute_tool
        mock_execute_tool.assert_called_once_with("sample_tool", {"query": "test"})

        # Second call checks
        second_call_args = mock_azure_client.chat.completions.create.call_args_list[1][1] # kwargs of second call
        messages_history = second_call_args['messages']
        self.assertEqual(len(messages_history), 3) # user_prompt, assistant_tool_call_request, tool_response
        self.assertEqual(messages_history[0]['role'], "user")
        self.assertEqual(messages_history[1]['role'], "assistant")
        self.assertIsNotNone(messages_history[1]['tool_calls'])
        self.assertEqual(messages_history[2]['role'], "tool")
        self.assertEqual(messages_history[2]['tool_call_id'], "call123")
        self.assertEqual(messages_history[2]['name'], "sample_tool")
        self.assertEqual(messages_history[2]['content'], json.dumps({"status": "success", "result": {"info": "tool result from mock"}}))


    @patch('app.routers.chat.execute_tool', new_callable=AsyncMock)
    @patch('app.routers.chat.TOOL_DEFINITIONS', [{"type": "function", "function": {"name": "sample_tool", "parameters": {}}}])
    @patch('app.routers.chat.client')
    def test_chat_completion_with_tool_call_tool_exec_fails(self, mock_azure_client, mock_tool_definitions_val, mock_execute_tool):
        mock_tool_call = ChatCompletionMessageToolCall(id="call_err", function=Function(name="error_tool", arguments='{}'), type="function")
        first_llm_response_msg = MagicMock(role="assistant", content=None, tool_calls=[mock_tool_call])
        first_llm_completion = MagicMock(choices=[MagicMock(message=first_llm_response_msg)], usage=MagicMock(), id="c1", created=1, model="m1")

        mock_execute_tool.return_value = {"status": "error", "error_message": "tool execution failed badly"}

        second_llm_response_msg = MagicMock(role="assistant", content="LLM summary of tool failure.", tool_calls=None)
        second_llm_completion = MagicMock(choices=[MagicMock(message=second_llm_response_msg)], usage=MagicMock(), id="c2", created=2, model="m1")
        mock_azure_client.chat.completions.create.side_effect = [first_llm_completion, second_llm_completion]

        payload = {**self.base_payload, "stream": False}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "LLM summary of tool failure.")

        mock_execute_tool.assert_called_once_with("error_tool", {})
        second_call_messages = mock_azure_client.chat.completions.create.call_args_list[1][1]['messages']
        self.assertEqual(second_call_messages[2]['role'], "tool")
        self.assertEqual(second_call_messages[2]['content'], json.dumps({"status": "error", "error_message": "tool execution failed badly"}))


    @patch('app.routers.chat.execute_tool', new_callable=AsyncMock)
    @patch('app.routers.chat.TOOL_DEFINITIONS', []) # No tools defined
    @patch('app.routers.chat.client')
    def test_chat_completion_no_tool_call_if_tools_not_defined(self, mock_azure_client, mock_tool_definitions_empty, mock_execute_tool):
        standard_llm_response_msg = MagicMock(role="assistant", content="Direct answer, no tools.", tool_calls=None)
        standard_llm_completion = MagicMock(choices=[MagicMock(message=standard_llm_response_msg)], usage=MagicMock(), id="c1", created=1, model="m1")
        mock_azure_client.chat.completions.create.return_value = standard_llm_completion

        payload = {**self.base_payload, "stream": False}
        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "Direct answer, no tools.")

        # Check that 'tools' and 'tool_choice' were NOT in the call to OpenAI
        call_kwargs = mock_azure_client.chat.completions.create.call_args[1]
        self.assertNotIn('tools', call_kwargs)
        self.assertNotIn('tool_choice', call_kwargs)
        mock_execute_tool.assert_not_called()

    @patch('app.routers.chat.execute_tool', new_callable=AsyncMock)
    @patch('app.routers.chat.TOOL_DEFINITIONS', [{"type": "function", "function": {"name": "sample_tool", "parameters": {}}}])
    @patch('app.routers.chat.client') # Mock the client for streaming test as well
    async def test_chat_completion_streaming_bypasses_tools(self, mock_azure_client, mock_tool_definitions_val, mock_execute_tool):
        # This test confirms current behavior: streaming ignores tools.
        # A more complex test would be needed if streaming + tools were fully supported.

        # Mock the asyncio.to_thread part of the streaming response
        mock_stream_chunk = MagicMock()
        mock_stream_chunk.choices = [MagicMock(delta=MagicMock(role="assistant", content="Streaming..."), finish_reason=None, index=0)]
        mock_stream_chunk.model = "stream_model"

        async def mock_stream():
            yield mock_stream_chunk
            # yield MagicMock(choices=[MagicMock(delta=MagicMock(content=" content."), finish_reason=None)])
            yield MagicMock(choices=[MagicMock(delta=MagicMock(content=None), finish_reason="stop", index=0)])


        # If client.chat.completions.create is directly called by asyncio.to_thread
        # its mock should be a regular MagicMock, not AsyncMock, if the original is sync
        if mock_azure_client: # Check if client is not None (due to settings)
            mock_azure_client.chat.completions.create = MagicMock(return_value=mock_stream())


        payload = {**self.base_payload, "stream": True}

        with patch('app.routers.chat.settings.AZURE_OPENAI_DEPLOYMENT', "mock_deployment_stream"):
            if not mock_azure_client : # If client is None, it will raise 500
                with self.assertRaises(HTTPException) as cm:
                    self.client.post("/v1/chat/completions", json=payload)
                self.assertEqual(cm.exception.status_code, 500)
                return

            response = self.client.post("/v1/chat/completions", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], "text/event-stream; charset=utf-8")

        # Check that execute_tool was not called
        mock_execute_tool.assert_not_called()

        # Check that tools were not passed to the OpenAI client call for streaming
        if mock_azure_client:
            call_kwargs = mock_azure_client.chat.completions.create.call_args[1]
            self.assertNotIn('tools', call_kwargs)
            self.assertNotIn('tool_choice', call_kwargs)


if __name__ == '__main__':
    unittest.main()
