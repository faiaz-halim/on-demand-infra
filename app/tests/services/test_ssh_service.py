import unittest
from unittest.mock import patch, MagicMock, mock_open, call # Ensure 'call' is imported
import paramiko
import socket
import os

from app.services.ssh_service import (
    execute_remote_command,
    upload_file_sftp,
    download_file_sftp
)

class TestSshService(unittest.TestCase):

    @patch('paramiko.SSHClient')
    @patch('os.path.exists', return_value=True)
    def test_execute_remote_command_success(self, mock_os_path_exists, MockSSHClient):
        mock_ssh_instance = MockSSHClient.return_value

        # Mock stdin, stdout, stderr for exec_command
        mock_stdin = MagicMock()
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()

        # Setup stdout and stderr channels
        mock_stdout.channel = MagicMock()
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stdout.read.return_value = b"test output"
        mock_stderr.read.return_value = b""

        mock_ssh_instance.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

        stdout, stderr, exit_code = execute_remote_command("host", "user", "/path/key", "ls -l")

        self.assertEqual(stdout, "test output")
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
        mock_ssh_instance.connect.assert_called_once_with("host", port=22, username="user", key_filename="/path/key", timeout=10, look_for_keys=False, allow_agent=False)
        mock_ssh_instance.exec_command.assert_called_once_with("ls -l")
        mock_ssh_instance.close.assert_called_once()

    @patch('paramiko.SSHClient')
    @patch('os.path.exists', return_value=True)
    def test_execute_remote_command_failure_exit_code(self, mock_os_path_exists, MockSSHClient):
        mock_ssh_instance = MockSSHClient.return_value
        mock_stdin = MagicMock()
        mock_stdout = MagicMock()
        mock_stderr = MagicMock()

        mock_stdout.channel = MagicMock()
        mock_stdout.channel.recv_exit_status.return_value = 1
        mock_stdout.read.return_value = b"test output on failure"
        mock_stderr.read.return_value = b"error occurred"

        mock_ssh_instance.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)

        stdout, stderr, exit_code = execute_remote_command("host", "user", "/path/key", "failing_cmd")

        self.assertEqual(stdout, "test output on failure")
        self.assertEqual(stderr, "error occurred")
        self.assertEqual(exit_code, 1)
        mock_ssh_instance.close.assert_called_once()

    @patch('paramiko.SSHClient')
    @patch('os.path.exists', return_value=True)
    def test_execute_remote_command_connect_raises_authentication_exception(self, mock_os_path_exists, MockSSHClient):
        mock_ssh_instance = MockSSHClient.return_value
        mock_ssh_instance.connect.side_effect = paramiko.AuthenticationException("Auth failed")

        stdout, stderr, exit_code = execute_remote_command("host", "user", "/path/key", "cmd")

        self.assertEqual(stdout, "")
        self.assertIn("Authentication failed", stderr) # Check substring
        self.assertEqual(exit_code, -1)
        mock_ssh_instance.close.assert_called_once()

    @patch('paramiko.SSHClient')
    @patch('os.path.exists', return_value=True)
    def test_execute_remote_command_connect_raises_ssh_exception(self, mock_os_path_exists, MockSSHClient):
        mock_ssh_instance = MockSSHClient.return_value
        mock_ssh_instance.connect.side_effect = paramiko.SSHException("SSH protocol error")

        stdout, stderr, exit_code = execute_remote_command("host", "user", "/path/key", "cmd")

        self.assertEqual(stdout, "")
        self.assertIn("SSH connection error", stderr)
        self.assertEqual(exit_code, -1)
        mock_ssh_instance.close.assert_called_once()

    @patch('paramiko.SSHClient')
    @patch('os.path.exists', return_value=True)
    def test_execute_remote_command_connect_raises_socket_timeout(self, mock_os_path_exists, MockSSHClient):
        mock_ssh_instance = MockSSHClient.return_value
        mock_ssh_instance.connect.side_effect = socket.timeout("Connection timed out")

        stdout, stderr, exit_code = execute_remote_command("host", "user", "/path/key", "cmd")

        self.assertEqual(stdout, "")
        self.assertIn("Connection timed out", stderr)
        self.assertEqual(exit_code, -1)
        mock_ssh_instance.close.assert_called_once()

    @patch('paramiko.SSHClient')
    @patch('os.path.exists', return_value=False)
    def test_execute_remote_command_key_file_not_found(self, mock_os_path_exists, MockSSHClient):
        # mock_os_path_exists is already configured by @patch to return False
        mock_ssh_instance = MockSSHClient.return_value

        stdout, stderr, exit_code = execute_remote_command("host", "user", "/nonexistent/key", "cmd")

        self.assertEqual(stdout, "")
        self.assertIn("Private key file not found", stderr)
        self.assertEqual(exit_code, -1)
        mock_ssh_instance.connect.assert_not_called()
        # The client is instantiated before the os.path.exists check in the function,
        # so close() will be called on it in the finally block.
        mock_ssh_instance.close.assert_called_once()


    @patch('paramiko.SSHClient')
    @patch('os.path.exists') # More granular control over side_effect
    def test_upload_file_sftp_success(self, mock_os_path_exists, MockSSHClient):
        # local_path exists, key_path exists
        mock_os_path_exists.side_effect = lambda path: True

        mock_ssh_instance = MockSSHClient.return_value
        mock_sftp_instance = MagicMock()
        mock_ssh_instance.open_sftp.return_value = mock_sftp_instance

        result = upload_file_sftp("host", "user", "/path/key", "/local/file.txt", "/remote/file.txt")

        self.assertTrue(result)
        mock_sftp_instance.put.assert_called_once_with("/local/file.txt", "/remote/file.txt")
        mock_sftp_instance.close.assert_called_once()
        mock_ssh_instance.close.assert_called_once()

    @patch('paramiko.SSHClient')
    @patch('os.path.exists', return_value=True)
    def test_upload_file_sftp_put_raises_exception(self, mock_os_path_exists, MockSSHClient):
        mock_ssh_instance = MockSSHClient.return_value
        mock_sftp_instance = MagicMock()
        mock_ssh_instance.open_sftp.return_value = mock_sftp_instance
        mock_sftp_instance.put.side_effect = IOError("SFTP put error")

        result = upload_file_sftp("host", "user", "/path/key", "/local/file.txt", "/remote/file.txt")

        self.assertFalse(result)
        mock_sftp_instance.close.assert_called_once() # Should still be called
        mock_ssh_instance.close.assert_called_once() # Should still be called

    @patch('paramiko.SSHClient')
    @patch('os.path.exists', side_effect=lambda path: path != "/local/file.txt") # local_path does NOT exist
    def test_upload_file_sftp_local_file_not_found(self, mock_os_path_exists, MockSSHClient):
        mock_ssh_instance = MockSSHClient.return_value
        result = upload_file_sftp("host", "user", "/path/key", "/local/file.txt", "/remote/file.txt")
        self.assertFalse(result)
        mock_ssh_instance.connect.assert_not_called()
        # If connect is not called, close on ssh_instance might not be called if instance itself is None
        # The current code initializes ssh_client = None, then ssh_client = paramiko.SSHClient()
        # So, close would be called. If it was initialized to None and only set on successful connect,
        # then close might not be called. Let's assume it's always instantiated.
        # Actually, the current code for upload/download doesn't init ssh_client to None first.
        # It directly does ssh_client = paramiko.SSHClient(). So close will be called.
        # However, if local_path doesn't exist, it returns early. So, no SSH connection, no close.
        mock_ssh_instance.close.assert_not_called()


    @patch('paramiko.SSHClient')
    @patch('os.path.exists', return_value=True)
    @patch('os.makedirs')
    def test_download_file_sftp_success(self, mock_os_makedirs, mock_os_path_exists, MockSSHClient):
        mock_ssh_instance = MockSSHClient.return_value
        mock_sftp_instance = MagicMock()
        mock_ssh_instance.open_sftp.return_value = mock_sftp_instance

        local_download_path = "/local/downloaded/file.txt"
        result = download_file_sftp("host", "user", "/path/key", "/remote/source.txt", local_download_path)

        self.assertTrue(result)
        # Check if dirname is not empty before asserting makedirs call
        if os.path.dirname(local_download_path):
            mock_os_makedirs.assert_called_once_with(os.path.dirname(local_download_path), exist_ok=True)
        else: # If local_path is just a filename in current dir, makedirs shouldn't be called
            mock_os_makedirs.assert_not_called()

        mock_sftp_instance.get.assert_called_once_with("/remote/source.txt", local_download_path)
        mock_sftp_instance.close.assert_called_once()
        mock_ssh_instance.close.assert_called_once()

    @patch('paramiko.SSHClient')
    @patch('os.path.exists', return_value=True)
    @patch('os.makedirs')
    def test_download_file_sftp_get_raises_exception(self, mock_os_makedirs, mock_os_path_exists, MockSSHClient):
        mock_ssh_instance = MockSSHClient.return_value
        mock_sftp_instance = MagicMock()
        mock_ssh_instance.open_sftp.return_value = mock_sftp_instance
        mock_sftp_instance.get.side_effect = IOError("SFTP get error")

        result = download_file_sftp("host", "user", "/path/key", "/remote/source.txt", "/local/file.txt")

        self.assertFalse(result)
        mock_sftp_instance.close.assert_called_once()
        mock_ssh_instance.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
