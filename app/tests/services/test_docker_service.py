import unittest
from unittest.mock import patch, MagicMock, call
import base64
import docker # For type hinting and APIError
from docker.errors import APIError

# Module to be tested
from app.services import docker_service
# For accessing logger if needed for assertLogs, or to silence it
from app.core.logging_config import get_logger

# Configure basic logging for test visibility if desired, or disable for tests
# logging.basicConfig(level=logging.DEBUG) # Example to see logs from service
# docker_service.logger.setLevel(logging.CRITICAL) # Example to silence service logs

class TestDockerServiceECR(unittest.TestCase):

    @patch('app.services.docker_service.boto3.client')
    def test_get_ecr_login_details_success(self, mock_boto_client):
        mock_ecr = MagicMock()
        mock_boto_client.return_value = mock_ecr

        auth_token_str = "AWS:verysecretpassword"
        auth_token_b64 = base64.b64encode(auth_token_str.encode('utf-8')).decode('utf-8')
        proxy_endpoint = "https://123456789012.dkr.ecr.us-east-1.amazonaws.com"

        mock_ecr.get_authorization_token.return_value = {
            'authorizationData': [{
                'authorizationToken': auth_token_b64,
                'proxyEndpoint': proxy_endpoint
            }]
        }

        details = docker_service.get_ecr_login_details(
            aws_region="us-east-1",
            aws_access_key_id="test_key_id",
            aws_secret_access_key="test_secret"
        )

        self.assertIsNotNone(details)
        username, password, endpoint = details
        self.assertEqual(username, "AWS")
        self.assertEqual(password, "verysecretpassword")
        self.assertEqual(endpoint, proxy_endpoint)
        mock_boto_client.assert_called_once_with(
            'ecr',
            region_name="us-east-1",
            aws_access_key_id="test_key_id",
            aws_secret_access_key="test_secret"
        )
        mock_ecr.get_authorization_token.assert_called_once()

    @patch('app.services.docker_service.boto3.client')
    def test_get_ecr_login_details_no_auth_data(self, mock_boto_client):
        mock_ecr = MagicMock()
        mock_boto_client.return_value = mock_ecr
        mock_ecr.get_authorization_token.return_value = {'authorizationData': []} # Empty list

        details = docker_service.get_ecr_login_details("us-east-1", "id", "secret")
        self.assertIsNone(details)

    @patch('app.services.docker_service.boto3.client')
    def test_get_ecr_login_details_boto_fails(self, mock_boto_client):
        mock_ecr = MagicMock()
        mock_boto_client.return_value = mock_ecr
        mock_ecr.get_authorization_token.side_effect = Exception("Boto3 exploded")

        details = docker_service.get_ecr_login_details("us-east-1", "id", "secret")
        self.assertIsNone(details)

    def test_login_to_ecr_success(self):
        mock_docker_client = MagicMock(spec=docker.DockerClient)
        mock_docker_client.login.return_value = {"Status": "Login Succeeded"}

        success = docker_service.login_to_ecr(
            mock_docker_client, "registry.example.com", "user", "pass"
        )
        self.assertTrue(success)
        mock_docker_client.login.assert_called_once_with(
            username="user", password="pass", registry="registry.example.com"
        )

    def test_login_to_ecr_failure_apierror(self):
        mock_docker_client = MagicMock(spec=docker.DockerClient)
        mock_docker_client.login.side_effect = APIError("Login failed from Docker")

        success = docker_service.login_to_ecr(
            mock_docker_client, "registry.example.com", "user", "pass"
        )
        self.assertFalse(success)

    def test_login_to_ecr_failure_exception(self):
        mock_docker_client = MagicMock(spec=docker.DockerClient)
        mock_docker_client.login.side_effect = Exception("Unexpected error")

        success = docker_service.login_to_ecr(
            mock_docker_client, "registry.example.com", "user", "pass"
        )
        self.assertFalse(success)

    def test_push_image_to_ecr_success(self):
        mock_docker_client = MagicMock(spec=docker.DockerClient)
        mock_image = MagicMock(spec=docker.models.images.Image)

        mock_docker_client.images.get.return_value = mock_image
        mock_image.tag.return_value = True # Docker SDK tag method returns bool

        # Simulate successful push stream
        push_stream = iter([
            {"status": "Preparing"},
            {"status": "Pushing", "progressDetail": {"current": 50, "total": 100}},
            {"status": "Pushed", "progressDetail": {"current": 100, "total": 100}},
            {"status": "latest: digest: sha256:abcdef123 size: 1234"}
        ])
        mock_docker_client.images.push.return_value = push_stream

        local_tag = "localapp:v1"
        ecr_repo = "my-app-repo"
        ecr_registry = "https://12345.dkr.ecr.us-region-1.amazonaws.com" # With https
        image_version = "v1.0.0"

        expected_ecr_uri_no_scheme = f"12345.dkr.ecr.us-region-1.amazonaws.com/{ecr_repo}:{image_version}"

        result_uri = docker_service.push_image_to_ecr(
            mock_docker_client, local_tag, ecr_repo, ecr_registry, image_version
        )

        self.assertEqual(result_uri, expected_ecr_uri_no_scheme)
        mock_docker_client.images.get.assert_called_once_with(local_tag)
        mock_image.tag.assert_called_once_with(expected_ecr_uri_no_scheme)
        mock_docker_client.images.push.assert_called_once_with(
            expected_ecr_uri_no_scheme, stream=True, decode=True
        )

    def test_push_image_to_ecr_tag_fails(self):
        mock_docker_client = MagicMock(spec=docker.DockerClient)
        mock_image = MagicMock(spec=docker.models.images.Image)
        mock_docker_client.images.get.return_value = mock_image
        mock_image.tag.return_value = False # Simulate tagging failure

        result_uri = docker_service.push_image_to_ecr(
            mock_docker_client, "local:tag", "repo", "https://reg.com"
        )
        self.assertIsNone(result_uri)

    def test_push_image_to_ecr_get_image_fails(self):
        mock_docker_client = MagicMock(spec=docker.DockerClient)
        mock_docker_client.images.get.side_effect = APIError("Image not found")

        result_uri = docker_service.push_image_to_ecr(
            mock_docker_client, "nonexistent:tag", "repo", "https://reg.com"
        )
        self.assertIsNone(result_uri)

    def test_push_image_to_ecr_push_stream_has_error_detail(self):
        mock_docker_client = MagicMock(spec=docker.DockerClient)
        mock_image = MagicMock(spec=docker.models.images.Image)
        mock_docker_client.images.get.return_value = mock_image
        mock_image.tag.return_value = True

        push_stream_with_error = iter([
            {"status": "Preparing"},
            {"errorDetail": {"message": "Access denied"}}
        ])
        mock_docker_client.images.push.return_value = push_stream_with_error

        result_uri = docker_service.push_image_to_ecr(
            mock_docker_client, "local:tag", "repo", "https://reg.com"
        )
        self.assertIsNone(result_uri)

    def test_push_image_to_ecr_push_stream_has_error_key(self):
        mock_docker_client = MagicMock(spec=docker.DockerClient)
        mock_image = MagicMock(spec=docker.models.images.Image)
        mock_docker_client.images.get.return_value = mock_image
        mock_image.tag.return_value = True

        push_stream_with_error = iter([
            {"status": "Preparing"},
            {"error": "Some generic push error"} # Simpler error format
        ])
        mock_docker_client.images.push.return_value = push_stream_with_error

        result_uri = docker_service.push_image_to_ecr(
            mock_docker_client, "local:tag", "repo", "https://reg.com"
        )
        self.assertIsNone(result_uri)

    def test_push_image_to_ecr_push_api_error(self):
        mock_docker_client = MagicMock(spec=docker.DockerClient)
        mock_image = MagicMock(spec=docker.models.images.Image)
        mock_docker_client.images.get.return_value = mock_image
        mock_image.tag.return_value = True
        mock_docker_client.images.push.side_effect = APIError("Push failed early")

        result_uri = docker_service.push_image_to_ecr(
            mock_docker_client, "local:tag", "repo", "https://reg.com"
        )
        self.assertIsNone(result_uri)

if __name__ == '__main__':
    unittest.main()
