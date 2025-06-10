import paramiko
import logging
import os
import socket # For socket.error
from typing import Tuple, Optional

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def execute_remote_command(
    hostname: str,
    username: str,
    private_key_path: str,
    command: str,
    port: int = 22,
    timeout: int = 10 # Connection timeout in seconds
) -> Tuple[str, str, int]:
    """
    Executes a command on a remote server via SSH using paramiko.
    """
    logger.info(f"Attempting to execute remote command on {username}@{hostname}:{port}: '{command}' using key {private_key_path}")

    if not os.path.exists(private_key_path):
        err_msg = f"Private key file not found at: {private_key_path}"
        logger.error(err_msg)
        return "", err_msg, -1

    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # Load private key
        # Ensure key is loaded before connect attempt if paramiko version requires it,
        # or pass it directly to connect if supported by the version for specific key types.
        # key = paramiko.RSAKey.from_private_key_file(private_key_path) # Example for RSA
        # For simplicity, key_filename in connect usually handles various key types.

        ssh_client.connect(
            hostname,
            port=port,
            username=username,
            key_filename=private_key_path, # Path to the private key file
            timeout=timeout,
            look_for_keys=False, # Important if private_key_path is the *only* method
            allow_agent=False    # Important if private_key_path is the *only* method
        )
        logger.info(f"SSH connection established to {username}@{hostname}:{port}")

        stdin, stdout, stderr = ssh_client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status() # Wait for command to complete and get exit status

        stdout_str = stdout.read().decode(errors='replace').strip()
        stderr_str = stderr.read().decode(errors='replace').strip()

        if exit_code == 0:
            logger.info(f"Remote command '{command}' executed successfully. Exit code: {exit_code}")
            logger.debug(f"Stdout:\n{stdout_str}")
            if stderr_str: # Log stderr even on success, as it might contain warnings
                 logger.debug(f"Stderr:\n{stderr_str}")
        else:
            logger.error(f"Remote command '{command}' failed. Exit code: {exit_code}")
            logger.error(f"Stdout:\n{stdout_str}")
            logger.error(f"Stderr:\n{stderr_str}")

        return stdout_str, stderr_str, exit_code

    except paramiko.AuthenticationException as e:
        err_msg = f"Authentication failed for {username}@{hostname}:{port} using key {private_key_path}: {str(e)}"
        logger.error(err_msg, exc_info=True)
        return "", err_msg, -1
    except paramiko.SSHException as e:
        err_msg = f"SSH connection error to {username}@{hostname}:{port}: {str(e)}"
        logger.error(err_msg, exc_info=True)
        return "", err_msg, -1
    except socket.timeout: # Specific timeout error
        err_msg = f"Connection timed out to {username}@{hostname}:{port} after {timeout} seconds."
        logger.error(err_msg, exc_info=True)
        return "", err_msg, -1
    except socket.error as e: # Other socket errors (e.g., connection refused)
        err_msg = f"Socket error connecting to {username}@{hostname}:{port}: {str(e)}"
        logger.error(err_msg, exc_info=True)
        return "", err_msg, -1
    except Exception as e: # Catch-all for other unexpected errors
        err_msg = f"An unexpected error occurred during remote command execution on {username}@{hostname}:{port}: {str(e)}"
        logger.error(err_msg, exc_info=True)
        return "", err_msg, -1
    finally:
        if ssh_client:
            ssh_client.close()
            logger.debug(f"SSH connection closed for {username}@{hostname}:{port}")


def upload_file_sftp(
    hostname: str,
    username: str,
    private_key_path: str,
    local_path: str,
    remote_path: str,
    port: int = 22,
    timeout: int = 10
) -> bool:
    """
    Uploads a local file to a remote server via SFTP using paramiko.
    """
    logger.info(f"Attempting to upload '{local_path}' to {username}@{hostname}:{port}:{remote_path} via SFTP using key {private_key_path}.")

    if not os.path.exists(local_path):
        logger.error(f"Local file not found for SFTP upload: {local_path}")
        return False
    if not os.path.exists(private_key_path): # Check for private key
        logger.error(f"Private key file not found at: {private_key_path}")
        return False

    ssh_client = None
    sftp_client = None
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(
            hostname,
            port=port,
            username=username,
            key_filename=private_key_path,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False
        )
        logger.debug(f"SFTP: SSH connection established to {username}@{hostname}:{port}")

        sftp_client = ssh_client.open_sftp()
        logger.debug("SFTP session opened.")

        # Ensure remote directory exists (paramiko sftp.put does not create parent dirs)
        # This is a common gotcha. For simplicity, assume remote parent dir exists or handle outside.
        # If needed: `sftp_client.mkdir(os.path.dirname(remote_path))` - needs error handling

        sftp_client.put(local_path, remote_path)
        logger.info(f"SFTP: Successfully uploaded '{local_path}' to '{remote_path}'.")
        return True

    except Exception as e: # Broad exception for SFTP specific errors, auth, connection etc.
        logger.error(f"SFTP upload failed: {str(e)}", exc_info=True)
        return False
    finally:
        if sftp_client:
            sftp_client.close()
            logger.debug("SFTP session closed.")
        if ssh_client:
            ssh_client.close()
            logger.debug(f"SFTP: SSH connection closed for {username}@{hostname}:{port}")


def download_file_sftp(
    hostname: str,
    username: str,
    private_key_path: str,
    remote_path: str,
    local_path: str,
    port: int = 22,
    timeout: int = 10
) -> bool:
    """
    Downloads a remote file to a local path via SFTP using paramiko.
    """
    logger.info(f"Attempting to download '{remote_path}' from {username}@{hostname}:{port} to '{local_path}' via SFTP using key {private_key_path}.")

    if not os.path.exists(private_key_path): # Check for private key
        logger.error(f"Private key file not found at: {private_key_path}")
        return False

    # Ensure local directory exists
    local_dir = os.path.dirname(local_path)
    if local_dir: # If local_path includes a directory part
        try:
            os.makedirs(local_dir, exist_ok=True)
            logger.debug(f"Ensured local directory '{local_dir}' exists for SFTP download.")
        except OSError as e:
            logger.error(f"Failed to create local directory '{local_dir}': {e}", exc_info=True)
            return False

    ssh_client = None
    sftp_client = None
    try:
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(
            hostname,
            port=port,
            username=username,
            key_filename=private_key_path,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False
        )
        logger.debug(f"SFTP: SSH connection established to {username}@{hostname}:{port}")

        sftp_client = ssh_client.open_sftp()
        logger.debug("SFTP session opened.")

        sftp_client.get(remote_path, local_path)
        logger.info(f"SFTP: Successfully downloaded '{remote_path}' to '{local_path}'.")
        return True

    except Exception as e:
        logger.error(f"SFTP download failed: {str(e)}", exc_info=True)
        return False
    finally:
        if sftp_client:
            sftp_client.close()
            logger.debug("SFTP session closed.")
        if ssh_client:
            ssh_client.close()
            logger.debug(f"SFTP: SSH connection closed for {username}@{hostname}:{port}")
