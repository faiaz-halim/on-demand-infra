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
    search_dirs = [repo_path] + [repo_path / subdir for subdir in ["app", "src", "service", "."]]

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

        expose_matches = re.findall(r"^\s*EXPOSE\s+((?:\d+(?:/(?:tcp|udp))?\s*)+)", content, re.IGNORECASE | re.MULTILINE)
        if expose_matches:
            ports: List[int] = []
            for match_group in expose_matches:
                individual_ports = re.findall(r"(\d+)(?:/(?:tcp|udp))?", match_group)
                for port_str in individual_ports:
                    try:
                        ports.append(int(port_str))
                    except ValueError:
                        logger.warning(f"Could not parse port number from EXPOSE instruction: {port_str}")
            if ports:
                analysis_results["exposed_ports"] = sorted(list(set(ports)))

        cmd_matches = re.findall(r"^\s*CMD\s+(.+)", content, re.IGNORECASE | re.MULTILINE)
        if cmd_matches:
            analysis_results["cmd"] = cmd_matches[-1].strip()

        entrypoint_matches = re.findall(r"^\s*ENTRYPOINT\s+(.+)", content, re.IGNORECASE | re.MULTILINE)
        if entrypoint_matches:
            analysis_results["entrypoint"] = entrypoint_matches[-1].strip()

        logger.info(f"Dockerfile analysis for {dockerfile_path}: {analysis_results}")

    except Exception as e:
        logger.error(f"Error analyzing Dockerfile {dockerfile_path}: {str(e)}", exc_info=True)

    return analysis_results

def check_dockerfile_best_practices(dockerfile_content: str) -> List[str]:
    """
    Performs basic checks on Dockerfile content for common best practice violations.
    Returns a list of warning messages.
    """
    warnings: List[str] = []

    # Check for sudo usage (often unnecessary and a security risk in Dockerfiles)
    if re.search(r"\bsudo\b", dockerfile_content, re.IGNORECASE): # \b for word boundary
        warnings.append("Use of 'sudo' detected. Consider if it's necessary; Docker builds often run as root or can grant permissions differently.")

    # Check for apt-get update without clean
    apt_update_pattern = r"RUN\s+(?:sudo\s+)?apt-get\s+update"
    apt_clean_pattern = r"rm\s+-rf\s+/var/lib/apt/lists/\*"
    if re.search(apt_update_pattern, dockerfile_content, re.IGNORECASE):
        # Check if a corresponding clean-up is NOT present in the same or subsequent RUN layer for apt.
        # This is a simplified check; a more robust check would analyze layers.
        if not re.search(apt_clean_pattern, dockerfile_content, re.IGNORECASE):
            warnings.append("'apt-get update' detected without a corresponding 'rm -rf /var/lib/apt/lists/*' to reduce image size.")

    # Check for non-specific base image tags (e.g., 'python' instead of 'python:3.9')
    # This regex looks for FROM instructions with single-word images (no tag or 'latest')
    # or images tagged as 'latest'.
    from_pattern_latest = r"^\s*FROM\s+([a-zA-Z0-9\-_./]+)(?::latest)?\s*(?:AS\s+\w+)?\s*$" # Matches 'image' or 'image:latest'

    lines = dockerfile_content.splitlines()
    for line_number, line in enumerate(lines): # Keep track of line numbers for context if needed
        line_strip = line.strip()
        if line_strip.upper().startswith("FROM"):
            # Try to extract image name and tag
            from_parts = line_strip.split()
            if len(from_parts) > 1:
                image_and_tag = from_parts[1]
                # Check if it's an alias (AS stage_name)
                if len(from_parts) > 3 and from_parts[2].upper() == "AS":
                    image_and_tag = from_parts[1]
                elif len(from_parts) == 2 : #e.g. FROM image_name
                     image_and_tag = from_parts[1]
                else: # e.g. FROM image_name AS alias_name
                    image_and_tag = from_parts[1]


                if ":" not in image_and_tag:
                    warnings.append(f"Base image '{image_and_tag}' does not have a specific version tag. Pin to a specific version (e.g., '{image_and_tag}:x.y.z').")
                elif image_and_tag.endswith(":latest"):
                    image_name = image_and_tag.split(":")[0]
                    warnings.append(f"Base image '{image_name}' uses 'latest' tag. Pin to a specific version for reproducible builds (e.g., '{image_name}:x.y.z').")


    # Check for USER instruction (absence of it means running as root)
    if not re.search(r"^\s*USER\s+", dockerfile_content, re.IGNORECASE | re.MULTILINE):
        warnings.append("No 'USER' instruction found. Consider adding a non-root user for security.")

    # Check for ADD command with remote URL (curl/wget is preferred for better control and layer management)
    if re.search(r"^\s*ADD\s+(?:http://|https://)", dockerfile_content, re.IGNORECASE | re.MULTILINE):
        warnings.append("Use of 'ADD' with a remote URL detected. Consider using 'RUN curl' or 'RUN wget' instead for better layer management and security.")

    if warnings:
        logger.info(f"Dockerfile best practice checks for content resulted in warnings: {warnings}")
    else:
        logger.info("Dockerfile content passed basic best practice checks.")

    return warnings

    # Example Usage (for testing this module):
    # if __name__ == "__main__":
    #     # ... (keep existing example usage for analyze_dockerfile if desired) ...
    #     import json # Added for example
    #     dockerfile_content_with_issues = """
    #     FROM ubuntu
    #     RUN apt-get update && apt-get install -y sudo
    #     RUN sudo apt-get install -y curl
    #     ADD https://example.com/somefile /tmp/somefile
    #     CMD ["/bin/bash"]
    #     """
    #     print("\n--- Checking Dockerfile with issues ---")
    #     issues = check_dockerfile_best_practices(dockerfile_content_with_issues)
    #     if issues:
    #         for issue in issues:
    #             print(f"Warning: {issue}")
    #     else:
    #         print("No issues found.")

    #     dockerfile_content_good = """
    #     FROM python:3.9-slim
    #     USER appuser
    #     RUN apt-get update && apt-get install --no-install-recommends -y curl && rm -rf /var/lib/apt/lists/*
    #     RUN curl -o /tmp/somefile https://example.com/somefile
    #     CMD ["/bin/bash"]
    #     """
    #     print("\n--- Checking good Dockerfile ---")
    #     issues_good = check_dockerfile_best_practices(dockerfile_content_good)
    #     if issues_good:
    #         for issue in issues_good:
    #             print(f"Warning: {issue}")
    #     else:
    #         print("No issues found.")

    # Example Usage (for testing this module):
    # if __name__ == "__main__":
    #     # Create dummy repo and Dockerfile for testing
    #     test_repo_dir = Path("./temp_test_repo_analyzer") # Changed name to avoid conflict
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
    #         import json # Ensure json is imported for example
    #         print(json.dumps(analysis, indent=2))

    #         print("\n--- Best Practice Checks ---")
    #         best_practice_warnings = check_dockerfile_best_practices(dummy_dockerfile.read_text())
    #         if best_practice_warnings:
    #             for warning in best_practice_warnings:
    #                 print(f"Warning: {warning}")
    #         else:
    #             print("No best practice issues found.")

    #     # Cleanup dummy repo
    #     import shutil
    #     try:
    #         shutil.rmtree(test_repo_dir)
    #         print(f"Cleaned up {test_repo_dir}")
    #     except Exception as e:
    #         print(f"Error cleaning up {test_repo_dir}: {e}")
    #     # print(f"Test Dockerfile and repo at {test_repo_dir} - remove manually if needed.")
