import unittest
from unittest.mock import patch, AsyncMock, MagicMock # AsyncMock for async functions
from pydantic import SecretStr

from app.core.schemas import AWSCredentials, ChatCompletionRequest, ChatMessage
from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment
)
import logging

# Disable verbose logging from services during these specific tests if desired,
# or use self.assertLogs for specific log checks.
# logging.disable(logging.INFO)

class TestOrchestrationService(unittest.IsolatedAsyncioTestCase): # Use IsolatedAsyncioTestCase for async test methods

    async def test_handle_local_deployment_no_creds_involved(self):
        mock_chat_request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="Deploy local please")]
        )
        repo_url = "http://github.com/test/local"
        namespace = "local-ns"

        # Expected message content based on the orchestration stub
        expected_message_content = f"Local deployment process started for {repo_url} in namespace {namespace}. Preparing environment..."

        # Call the function
        response_dict = await handle_local_deployment(repo_url, namespace, mock_chat_request)

        # Assert appended message
        self.assertTrue(len(mock_chat_request.messages) > 1)
        appended_message = mock_chat_request.messages[-1]
        self.assertEqual(appended_message.role, "assistant")
        self.assertEqual(appended_message.content, expected_message_content)

        # Assert returned dictionary
        self.assertEqual(response_dict["status"], "success")
        self.assertEqual(response_dict["mode"], "local")
        self.assertEqual(response_dict["message"], expected_message_content)


    @patch('app.services.orchestration_service.logger') # Patch logger to check log messages
    async def test_handle_cloud_local_deployment_creds_handling(self, mock_logger):
        mock_aws_creds = AWSCredentials(
            aws_access_key_id=SecretStr("test_access_key"),
            aws_secret_access_key=SecretStr("test_secret_key"),
            aws_region="us-east-1"
        )
        mock_chat_request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="Deploy cloud-local please")]
        )
        repo_url = "http://github.com/test/cloudlocal"
        namespace = "cl-ns"

        # Expected message content based on the orchestration stub
        expected_message_content = f"Cloud-local deployment process started for {repo_url} in namespace {namespace}. AWS credentials received for region {mock_aws_creds.aws_region}."

        # Call the function
        response_dict = await handle_cloud_local_deployment(repo_url, namespace, mock_aws_creds, mock_chat_request)

        # Assert appended message
        self.assertTrue(len(mock_chat_request.messages) > 1)
        appended_message = mock_chat_request.messages[-1]
        self.assertEqual(appended_message.role, "assistant")
        self.assertEqual(appended_message.content, expected_message_content)

        # Assert logger call for AWS env var preparation
        # Example: logger.info(f"AWS environment variables prepared for cloud-local deployment. Region: {aws_creds.aws_region}. (Secrets are not logged).")
        log_found = False
        for call_args in mock_logger.info.call_args_list:
            if f"AWS environment variables prepared for cloud-local deployment. Region: {mock_aws_creds.aws_region}" in call_args[0][0]:
                log_found = True
                break
        self.assertTrue(log_found, "Log message for AWS env var preparation not found or incorrect.")

        # Assert returned dictionary
        self.assertEqual(response_dict["status"], "success")
        self.assertEqual(response_dict["mode"], "cloud-local")
        self.assertEqual(response_dict["aws_region_processed"], mock_aws_creds.aws_region)


    @patch('app.services.orchestration_service.logger') # Patch logger
    async def test_handle_cloud_hosted_deployment_creds_handling(self, mock_logger):
        mock_aws_creds = AWSCredentials(
            aws_access_key_id=SecretStr("another_access_key"),
            aws_secret_access_key=SecretStr("another_secret_key"),
            aws_region="eu-west-1"
        )
        mock_chat_request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="Deploy cloud-hosted please")]
        )
        repo_url = "http://github.com/test/cloudhosted"
        namespace = "ch-ns"

        # Expected message content based on the orchestration stub
        expected_message_content = f"Cloud-hosted (EKS) deployment process started for {repo_url} in namespace {namespace}. AWS credentials received for region {mock_aws_creds.aws_region}."

        # Call the function
        response_dict = await handle_cloud_hosted_deployment(repo_url, namespace, mock_aws_creds, mock_chat_request)

        # Assert appended message
        self.assertTrue(len(mock_chat_request.messages) > 1)
        appended_message = mock_chat_request.messages[-1]
        self.assertEqual(appended_message.role, "assistant")
        self.assertEqual(appended_message.content, expected_message_content)

        # Assert logger call for AWS env var preparation
        log_found = False
        for call_args in mock_logger.info.call_args_list:
            if f"AWS environment variables prepared for cloud-hosted deployment. Region: {mock_aws_creds.aws_region}" in call_args[0][0]:
                log_found = True
                break
        self.assertTrue(log_found, "Log message for AWS env var preparation not found or incorrect.")

        # Assert returned dictionary
        self.assertEqual(response_dict["status"], "success")
        self.assertEqual(response_dict["mode"], "cloud-hosted")
        self.assertEqual(response_dict["aws_region_processed"], mock_aws_creds.aws_region)


if __name__ == '__main__':
    unittest.main()
