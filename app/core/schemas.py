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

    # --- Custom MCP Server parameters ---
    # Deployment target and mode
    github_repo_url: Optional[str] = Field(default=None, description="URL of the GitHub repository to deploy. If provided, an action is expected.")
    deployment_mode: Optional[Literal["local", "cloud-local", "cloud-hosted"]] = Field(default="local", description="The desired deployment mode.")
    target_namespace: Optional[str] = Field(default="default", description="Target Kubernetes namespace for the deployment.")

    # Lifecycle action and parameters
    action: Optional[Literal["deploy", "redeploy", "scale", "decommission"]] = Field(
        default="deploy",
        description="The lifecycle action to perform. Defaults to 'deploy' if github_repo_url is provided. Other actions typically require 'instance_id'."
    )
    instance_id: Optional[str] = Field(
        default=None,
        description="Identifier of an existing instance/deployment to manage (e.g., for redeploy, scale, decommission). This often corresponds to the 'instance_name_tag' used during creation."
    )
    scale_replicas: Optional[int] = Field(
        default=None,
        description="Number of replicas to scale to for the 'scale' action."
    )

    # Cloud-specific parameters
    aws_credentials: Optional[AWSCredentials] = Field(default=None, description="AWS credentials, required for cloud-local and cloud-hosted modes.")
    ec2_key_name: Optional[str] = Field(default=None, description="Name of the EC2 key pair to use for SSH access. Required for cloud-local mode if not set in server defaults.")
    public_ip: Optional[str] = Field(default=None, description="Public IP of the EC2 instance to manage (e.g. for redeploy, scale actions on an existing cloud-local instance).")
    # instance_size: Optional[str] = Field(default=None, description="EC2 instance size for cloud-local mode. Overrides server default.")

    # Cloud-Hosted EKS specific DNS/SSL parameters
    base_hosted_zone_id: Optional[str] = Field(
        default=None,
        title="Route53 Base Hosted Zone ID",
        description="The Route53 Hosted Zone ID for the base domain under which the app's subdomain will be created (e.g., Z0123456789ABCDEFGHIJ for 'example.com'). Required if custom domain/SSL is desired for cloud-hosted EKS deployments."
    )
    app_subdomain_label: Optional[str] = Field(
        default=None,
        title="Application Subdomain Label",
        description="The label for the application's subdomain (e.g., 'myapp'). If provided, the full app domain will be 'myapp.your_base_domain.com'. If not provided, a name might be derived from the repository name. Used for cloud-hosted EKS deployments with custom domain/SSL."
    )

    # Application configuration (example, can be expanded)
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
