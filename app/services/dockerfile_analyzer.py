import re
from pathlib import Path
from typing import Optional, Dict, List, Union
from app.core.logging_config import get_logger

logger = get_logger(__name__)

def detect_dockerfile(repo_path: Union[str, Path]) -> Optional[Path]:
    """
    Detects a Dockerfile in the given repository path.
    Searches for 'Dockerfile', 'dockerfile', and in common subdirectories.
    Returns the Path object to the Dockerfile if found, else None.
    """
    repo_path = Path(repo_path)
    possible_names = ["Dockerfile", "dockerfile"]
    search_dirs = [repo_path] + [repo_path / subdir for subdir in ["app", "src", "service", "."]] # Include root again for flat structures

    for name in possible_names:
        for directory in search_dirs:
            dockerfile_path = directory / name
            if dockerfile_path.is_file():
                logger.info(f"Dockerfile found at: {dockerfile_path}")
                return dockerfile_path

    logger.warning(f"No Dockerfile found in common locations within {repo_path}")
    return None

def analyze_dockerfile(dockerfile_path: Path) -> Dict[str, Union[Optional[List[int]], Optional[str]]]:
    """
    Analyzes a Dockerfile to extract EXPOSE, CMD, and ENTRYPOINT instructions.
    """
    analysis_results: Dict[str, Union[Optional[List[int]], Optional[str]]] = {
        "exposed_ports": None,
        "cmd": None,
        "entrypoint": None
    }

    if not dockerfile_path.is_file():
        logger.error(f"Dockerfile path does not exist or is not a file: {dockerfile_path}")
        return analysis_results

    try:
        content = dockerfile_path.read_text()

        # Extract EXPOSE instructions (can be multiple ports on one line or multiple EXPOSE lines)
        # Handles formats like EXPOSE 80, EXPOSE 80/tcp, EXPOSE 80 443
        expose_matches = re.findall(r"^\s*EXPOSE\s+((?:\d+(?:/(?:tcp|udp))?\s*)+)", content, re.IGNORECASE | re.MULTILINE)
        if expose_matches:
            ports: List[int] = []
            for match_group in expose_matches:
                # Split multiple ports on the same line and extract only the port number
                individual_ports = re.findall(r"(\d+)(?:/(?:tcp|udp))?", match_group)
                for port_str in individual_ports:
                    try:
                        ports.append(int(port_str))
                    except ValueError:
                        logger.warning(f"Could not parse port number from EXPOSE instruction: {port_str}")
            if ports:
                analysis_results["exposed_ports"] = sorted(list(set(ports))) # Unique, sorted ports

        # Extract last CMD instruction (handles both JSON and shell forms)
        # CMD ["executable","param1","param2"] (JSON form)
        # CMD command param1 param2 (shell form)
        cmd_matches = re.findall(r"^\s*CMD\s+(.+)", content, re.IGNORECASE | re.MULTILINE)
        if cmd_matches:
            analysis_results["cmd"] = cmd_matches[-1].strip() # Get the last one

        # Extract last ENTRYPOINT instruction (handles both JSON and shell forms)
        entrypoint_matches = re.findall(r"^\s*ENTRYPOINT\s+(.+)", content, re.IGNORECASE | re.MULTILINE)
        if entrypoint_matches:
            analysis_results["entrypoint"] = entrypoint_matches[-1].strip() # Get the last one

        logger.info(f"Dockerfile analysis for {dockerfile_path}: {analysis_results}")

    except Exception as e:
        logger.error(f"Error analyzing Dockerfile {dockerfile_path}: {str(e)}", exc_info=True)
        # Return partially filled or empty results in case of error during parsing

    return analysis_results

# Example Usage (for testing this module):
# if __name__ == "__main__":
#     # Create dummy repo and Dockerfile for testing
#     test_repo_dir = Path("./temp_test_repo")
#     test_repo_dir.mkdir(exist_ok=True)
#     dockerfile_content = """
#     FROM python:3.9-slim
#     WORKDIR /app
#     COPY . .
#     RUN pip install -r requirements.txt
#     EXPOSE 8000
#     EXPOSE 8080/tcp 443
#     ENV NAME World
#     # This is a comment
#     ENTRYPOINT ["python", "app/main.py"]
#     CMD ["--default-param"]
#     # Another ENTRYPOINT to test overriding
#     ENTRYPOINT ["/usr/local/bin/my-entrypoint.sh"]
#     # Another CMD
#     CMD echo "Hello $NAME"
#     """
#     dummy_dockerfile = test_repo_dir / "Dockerfile"
#     with open(dummy_dockerfile, "w") as f:
#         f.write(dockerfile_content)

#     found_df_path = detect_dockerfile(test_repo_dir)
#     if found_df_path:
#         analysis = analyze_dockerfile(found_df_path)
#         print("Analysis Results:")
#         import json
#         print(json.dumps(analysis, indent=2))

#     # Cleanup dummy repo
#     import shutil
#     # shutil.rmtree(test_repo_dir)
#     print(f"Test Dockerfile and repo at {test_repo_dir} - remove manually if needed.")
