import os
import re
import subprocess
import logging
import tempfile
import shutil
from pydantic import BaseModel
from typing import List, Optional

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

class GitHubRepoAnalysisModel(BaseModel):
    """Model to store GitHub repository analysis results"""
    repo_url: str
    has_dockerfile: bool = False
    build_commands: List[str] = []
    run_commands: List[str] = []
    error: Optional[str] = None

class GitHubService:
    """Service for GitHub repository analysis operations"""

    def analyze_repo(self, repo_url: str) -> GitHubRepoAnalysisModel:
        """Main method to analyze a GitHub repository"""
        from .security_utils import sanitize_shell_input

        # Sanitize the repository URL
        sanitized_url = sanitize_shell_input(repo_url)
        if sanitized_url is None:
            return GitHubRepoAnalysisModel(
                repo_url=repo_url,
                error="Invalid repository URL detected"
            )

        # Create a temporary directory for cloning
        temp_dir = tempfile.mkdtemp()

        try:
            # Clone the repository using the sanitized URL
            self.clone_repo(sanitized_url, temp_dir)

            # Check for Dockerfile
            has_dockerfile = self.detect_dockerfile(temp_dir)

            # Parse README for commands
            readme_commands = self.parse_readme(temp_dir)
            build_commands = readme_commands["build_commands"]
            run_commands = readme_commands["run_commands"]

            # Create analysis result
            analysis = GitHubRepoAnalysisModel(
                repo_url=repo_url,
                has_dockerfile=has_dockerfile,
                build_commands=build_commands,
                run_commands=run_commands
            )

            logger.info(f"Analysis completed for {repo_url}")
            return analysis
        except Exception as e:
            logger.error(f"Error analyzing repository: {str(e)}")
            return GitHubRepoAnalysisModel(
                repo_url=repo_url,
                error=str(e)
            )
        finally:
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                logger.info(f"Cleaned up temporary directory: {temp_dir}")
            except Exception as e:
                logger.error(f"Error cleaning up temporary directory: {str(e)}")

    def clone_repo(self, repo_url: str, local_path: str) -> None:
        """Clone a GitHub repository to a local directory"""
        try:
            # Create directory if it doesn't exist
            os.makedirs(local_path, exist_ok=True)

            # Run git clone command
            subprocess.run(
                ["git", "clone", repo_url, local_path],
                check=True,
                capture_output=True,
                text=True
            )
            logger.info(f"Successfully cloned repository: {repo_url}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to clone repository {repo_url}: {e.stderr}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error cloning repository: {str(e)}")
            raise

    def detect_dockerfile(self, local_path: str) -> bool:
        """Check if a Dockerfile exists in the repository"""
        dockerfile_path = os.path.join(local_path, "Dockerfile")
        if os.path.exists(dockerfile_path):
            logger.info(f"Dockerfile found at: {dockerfile_path}")
            return True
        logger.info("No Dockerfile found in the repository")
        return False

    def parse_readme(self, local_path: str) -> dict:
        """Parse README.md for build and run commands"""
        readme_path = [
            os.path.join(local_path, "README.md"),
            os.path.join(local_path, "readme.md"),
            os.path.join(local_path, "Readme.md")
        ]

        build_commands = []
        run_commands = []

        for path in readme_path:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        content = f.read()

                        # Look for build commands (common keywords)
                        build_pattern = r'(?:build|install|compile|make).*?```(?:bash|shell)?\n(.*?)\n```'
                        build_matches = re.findall(build_pattern, content, re.IGNORECASE | re.DOTALL)
                        build_commands = [cmd.strip() for match in build_matches for cmd in match.split('\n') if cmd.strip()]

                        # Look for run commands (common keywords)
                        run_pattern = r'(?:run|start|execute|launch).*?```(?:bash|shell)?\n(.*?)\n```'
                        run_matches = re.findall(run_pattern, content, re.IGNORECASE | re.DOTALL)
                        run_commands = [cmd.strip() for match in run_matches for cmd in match.split('\n') if cmd.strip()]

                        logger.info(f"Found {len(build_commands)} build commands and {len(run_commands)} run commands in README")
                        break
                except Exception as e:
                    logger.error(f"Error reading README file: {str(e)}")

        return {
            "build_commands": build_commands,
            "run_commands": run_commands
        }
