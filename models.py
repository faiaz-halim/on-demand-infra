from typing import Literal, Optional, List, Union
from pydantic import BaseModel

class APIRequestModel(BaseModel):
    """Model for the /v1/chat/completions request body"""
    prompt: str
    github_url: str
    deployment_mode: Literal['local', 'cloud-local', 'cloud-hosted']
    aws_credentials: Optional[dict] = None

class GitHubRepoAnalysisModel(BaseModel):
    """Model to store GitHub repository analysis results"""
    repo_url: str
    local_path: str
    has_dockerfile: bool = False
    build_commands: List[str] = []
    run_commands: List[str] = []
    dockerfile_path: Optional[str] = None

class StreamingMessage(BaseModel):
    """Model for streaming status updates and errors"""
    status: Literal['running', 'completed', 'error']
    current_step: str
    message: str
    error_type: Optional[str] = None
    error_details: Optional[dict] = None
