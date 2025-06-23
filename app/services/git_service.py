import subprocess
import shutil
import os
import uuid
from pathlib import Path
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# Define a root for workspaces, e.g., relative to this file's location or project root
# For simplicity, let's assume a 'workspaces' directory in the project root.
# This path might need adjustment depending on where the script is run from.
# It's often better to configure this path via settings.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent # This assumes app/services/git_service.py
WORKSPACES_DIR = PROJECT_ROOT / "workspaces"

class GitCloneError(Exception):
    """Custom exception for git clone failures."""
    def __init__(self, message, return_code=None, stderr=None):
        super().__init__(message)
        self.return_code = return_code
        self.stderr = stderr

def create_temp_workspace() -> Path:
    """Creates a unique temporary workspace directory."""
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    workspace_path = WORKSPACES_DIR / str(uuid.uuid4())
    workspace_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Created temporary workspace: {workspace_path}")
    return workspace_path

def clone_repository(repo_url: str, target_workspace: Path) -> Path:
    """
    Clones a public GitHub repository into the specified target directory.
    Returns the path to the cloned repository.
    Raises GitCloneError on failure.
    """
    if not repo_url or not repo_url.startswith("https://github.com/"):
        raise ValueError("Invalid GitHub repository URL provided.")

    # Extract repo name to create a subdirectory within the workspace
    try:
        repo_name = repo_url.split('/')[-1].replace('.git', '')
        if not repo_name: # Handle cases like https://github.com/user/ (though unlikely)
             repo_name = "repository"
    except IndexError:
        repo_name = "repository" # Default if parsing fails

    clone_to_path = target_workspace / repo_name

    logger.info(f"Attempting to clone {repo_url} into {clone_to_path}...")
    try:
        # Ensure target_workspace exists (should be handled by create_temp_workspace)
        target_workspace.mkdir(parents=True, exist_ok=True)

        # Using subprocess to call git CLI
        # SECURITY NOTE: Always be cautious with subprocess and external commands.
        # Here, repo_url is user-provided but somewhat validated.
        # Consider depth, disabling prompts, etc. for more robustness.
        process = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(clone_to_path)],
            capture_output=True,
            text=True,
            check=False # We check returncode manually
        )

        if process.returncode != 0:
            error_message = f"Failed to clone repository: {repo_url}. Error: {process.stderr}"
            logger.error(error_message)
            raise GitCloneError(error_message, return_code=process.returncode, stderr=process.stderr)

        logger.info(f"Successfully cloned {repo_url} to {clone_to_path}")
        return clone_to_path

    except FileNotFoundError: # git command not found
        logger.error("Git command not found. Ensure git is installed and in PATH.")
        raise GitCloneError("Git command not found. Ensure git is installed and in PATH.")
    except Exception as e:
        logger.error(f"An unexpected error occurred during cloning: {str(e)}")
        raise GitCloneError(f"An unexpected error occurred during cloning: {str(e)}")


def remove_workspace(workspace_path: Path):
    """Removes the specified workspace directory."""
    if workspace_path and workspace_path.exists() and workspace_path.is_dir():
        try:
            shutil.rmtree(workspace_path)
            logger.info(f"Successfully removed workspace: {workspace_path}")
        except Exception as e:
            logger.error(f"Error removing workspace {workspace_path}: {str(e)}")
    else:
        logger.warning(f"Workspace path {workspace_path} not found or not a directory.")

# Example usage (for testing this module directly):
# if __name__ == "__main__":
#     test_repo_url = "https://github.com/jules-agent/app-server-test-repo.git" # A sample public repo
#     # test_repo_url = "https://github.com/pallets/flask.git" # A larger public repo
#     # test_repo_url_invalid = "https://invalid.url/foo/bar.git"

#     temp_ws = None
#     try:
#         temp_ws = create_temp_workspace()
#         cloned_path = clone_repository(test_repo_url, temp_ws)
#         print(f"Repository cloned to: {cloned_path}")

#         # Test with invalid URL
#         # clone_repository(test_repo_url_invalid, temp_ws)

#     except ValueError as ve:
#         print(f"ValueError: {ve}")
#     except GitCloneError as e:
#         print(f"GitCloneError: {e}")
#         if e.stderr:
#             print(f"Stderr: {e.stderr}")
#     finally:
#         if temp_ws:
#             # remove_workspace(temp_ws) # Keep for inspection if running manually
#             print(f"Workspace kept at: {temp_ws} for inspection. Remove manually.")
#             pass
