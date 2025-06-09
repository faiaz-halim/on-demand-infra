from pydantic import BaseModel, Field, SecretStr
from typing import List, Optional, Union, Literal, Dict, Any

# Based on OpenAI API documentation for chat completions

class AWSCredentials(BaseModel):
    aws_access_key_id: SecretStr
    aws_secret_access_key: SecretStr
    aws_region: str

    model_config = {'extra': 'ignore'}


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    name: Optional[str] = None  # For tool role if needed
    tool_call_id: Optional[str] = None # For tool role if needed
    # tool_calls: Optional[List[Any]] = None # For assistant role if it makes tool calls

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = "mcp-server-default" # Can be used for internal routing
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    logit_bias: Optional[Dict[str, float]] = None
    user: Optional[str] = None

    # Custom MCP Server parameters
    github_repo_url: Optional[str] = Field(default=None, description="URL of the GitHub repository to deploy.")
    deployment_mode: Optional[Literal["local", "cloud-local", "cloud-hosted"]] = Field(default="local", description="The desired deployment mode.")
    aws_credentials: Optional[AWSCredentials] = Field(default=None, description="AWS credentials, required for cloud-local and cloud-hosted modes.")
    target_namespace: Optional[str] = Field(default="default", description="Target Kubernetes namespace for the deployment.")

    # Cloud-local specific parameters
    ec2_key_name: Optional[str] = Field(default=None, description="Name of the EC2 key pair to use for SSH access. Required for cloud-local mode if not set in server defaults.")
    # instance_size: Optional[str] = Field(default=None, description="EC2 instance size for cloud-local mode. Overrides server default.")
    # application_environment_variables: Optional[Dict[str, str]] = Field(default_factory=dict, description="Environment variables for the application.")


class ChoiceDelta(BaseModel):
    role: Optional[Literal["system", "user", "assistant"]] = None
    content: Optional[str] = None
    # tool_calls: Optional[List[Any]] = None


class ChatCompletionStreamChoice(BaseModel):
    index: int
    delta: ChoiceDelta
    finish_reason: Optional[str] = None


class ChatCompletionStreamResponse(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int # Unix timestamp
    model: str
    choices: List[ChatCompletionStreamChoice]
    # system_fingerprint: Optional[str] = None # If needed


class Choice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str # e.g., "chatcmpl-..."
    object: Literal["chat.completion"] = "chat.completion"
    created: int # Unix timestamp
    model: str # Model used
    choices: List[Choice]
    usage: Usage
    # system_fingerprint: Optional[str] = None # If needed
