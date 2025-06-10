from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List, Dict, Any
import json
import pathlib

class Settings(BaseSettings):
    # Azure OpenAI API settings
    AZURE_OPENAI_API_KEY: Optional[str] = None
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    AZURE_OPENAI_API_VERSION: Optional[str] = "2023-12-01-preview"
    AZURE_OPENAI_DEPLOYMENT: Optional[str] = None

    # Azure OpenAI Embedding settings
    AZURE_EMBEDDING_API_VERSION: Optional[str] = None
    AZURE_EMBEDDING_DEPLOYMENT: Optional[str] = None
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT: Optional[str] = None

    # Langchain settings
    LANGCHAIN_API_KEY: Optional[str] = None
    LANGCHAIN_ENDPOINT: Optional[str] = None
    LANGCHAIN_PROJECT: Optional[str] = None
    LANGCHAIN_TRACING_V2: Optional[str] = "false"

    # AWS settings
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: Optional[str] = "us-east-1"

    # Logging configuration
    LOG_LEVEL: str = "INFO"

    # Kind Cluster settings
    KIND_CLUSTER_NAME: str = "on-demand-infra"
    KIND_CALICO_MANIFEST_URL: Optional[str] = "https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml"
    DEFAULT_KIND_VERSION: str = "0.23.0"
    DEFAULT_KUBECTL_VERSION: str = "1.30.0"

    # EC2 Default settings for Cloud-Local
    EC2_DEFAULT_AMI_ID: str = "ami-00c39f71452c08778"
    EC2_DEFAULT_INSTANCE_TYPE: str = "t3.medium"
    EC2_DEFAULT_KEY_NAME: Optional[str] = None
    EC2_DEFAULT_APP_PORTS_JSON: str = '[{"port": 80, "protocol": "tcp"}]'

    # EC2 SSH settings
    EC2_SSH_USERNAME: str = "ec2-user"
    EC2_PRIVATE_KEY_BASE_PATH: Optional[str] = None
    EC2_DEFAULT_REPO_PATH: str = "/home/ec2-user/app_repo"
    EC2_DEFAULT_REMOTE_MANIFEST_PATH: str = "/tmp/mcp_manifests"

    # EKS Default settings for Cloud-Hosted
    EKS_DEFAULT_VERSION: str = "1.29"
    EKS_DEFAULT_NODE_INSTANCE_TYPE: str = "t3.medium"
    EKS_DEFAULT_NODE_DESIRED_SIZE: int = 1 # Renamed from EKS_DEFAULT_NODE_DESIRED_COUNT and value changed
    EKS_DEFAULT_NODE_MIN_SIZE: int = 1 # Renamed from EKS_DEFAULT_NODE_MIN_COUNT
    EKS_DEFAULT_NODE_MAX_SIZE: int = 2 # Renamed from EKS_DEFAULT_NODE_MAX_COUNT and value changed
    EKS_DEFAULT_VPC_CIDR: str = "10.0.0.0/16"
    EKS_DEFAULT_NUM_PUBLIC_SUBNETS: int = 2
    EKS_DEFAULT_NUM_PRIVATE_SUBNETS: int = 2
    EKS_DEFAULT_CLUSTER_NAME_PREFIX: str = "mcp-eks"
    EKS_DEFAULT_NODE_GROUP_NAME_SUFFIX: str = "ng-default"

    # ECR Defaults
    ECR_DEFAULT_IMAGE_TAG_MUTABILITY: str = "MUTABLE"
    ECR_DEFAULT_SCAN_ON_PUSH: bool = True
    ECR_DEFAULT_REPO_NAME_PREFIX: str = "mcp-app"

    # Persistent workspace for Terraform states, etc.
    PERSISTENT_WORKSPACE_BASE_DIR: str = "/app/mcp_workspaces"


    @property
    def EC2_DEFAULT_APP_PORTS(self) -> List[Dict[str, Any]]:
        try:
            ports = json.loads(self.EC2_DEFAULT_APP_PORTS_JSON)
            if not isinstance(ports, list):
                print(f"Warning: EC2_DEFAULT_APP_PORTS_JSON ('{self.EC2_DEFAULT_APP_PORTS_JSON}') is not a valid JSON list. Using empty list.")
                return []
            for item in ports:
                if not isinstance(item, dict) or "port" not in item or "protocol" not in item:
                    print(f"Warning: Invalid item in EC2_DEFAULT_APP_PORTS_JSON: {item}. Must be dict with 'port' and 'protocol'. Using empty list.")
                    return []
            return ports
        except json.JSONDecodeError:
            print(f"Warning: Failed to parse EC2_DEFAULT_APP_PORTS_JSON ('{self.EC2_DEFAULT_APP_PORTS_JSON}'). Using default empty list.")
            return []

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding='utf-8', extra='ignore')

settings = Settings()
