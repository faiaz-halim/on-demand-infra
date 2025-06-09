from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, List, Dict, Any
import json

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
    AWS_REGION: Optional[str] = "us-east-1" # Default region

    # Logging configuration
    LOG_LEVEL: str = "INFO"

    # Kind Cluster settings
    KIND_CLUSTER_NAME: str = "on-demand-infra" # Default name for Kind cluster if created by MCP
    KIND_CALICO_MANIFEST_URL: Optional[str] = "https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml"
    DEFAULT_KIND_VERSION: str = "0.23.0"
    DEFAULT_KUBECTL_VERSION: str = "1.30.0"

    # EC2 Default settings for Cloud-Local
    # Example AMI ID for Amazon Linux 2 (HVM), SSD Volume Type in us-east-1.
    # This is a common one, but users should verify/update for their specific region and needs.
    EC2_DEFAULT_AMI_ID: str = "ami-00c39f71452c08778"
    EC2_DEFAULT_INSTANCE_TYPE: str = "t3.medium" # Updated as per subtask
    # CRITICAL: User must ensure this key pair exists in their target AWS account and region.
    # Setting to None by default to force configuration either via .env or API parameter.
    EC2_DEFAULT_KEY_NAME: Optional[str] = None
    EC2_DEFAULT_APP_PORTS_JSON: str = '[{"port": 80, "protocol": "tcp"}]'

    @property
    def EC2_DEFAULT_APP_PORTS(self) -> List[Dict[str, Any]]:
        try:
            ports = json.loads(self.EC2_DEFAULT_APP_PORTS_JSON)
            if not isinstance(ports, list):
                # Consider logging this warning with the application's logger
                print(f"Warning: EC2_DEFAULT_APP_PORTS_JSON ('{self.EC2_DEFAULT_APP_PORTS_JSON}') is not a valid JSON list. Using empty list.")
                return []
            for item in ports:
                if not isinstance(item, dict) or "port" not in item or "protocol" not in item:
                    # Consider logging this warning
                    print(f"Warning: Invalid item in EC2_DEFAULT_APP_PORTS_JSON: {item}. Must be dict with 'port' and 'protocol'. Using empty list.")
                    return []
            return ports
        except json.JSONDecodeError:
            print(f"Warning: Failed to parse EC2_DEFAULT_APP_PORTS_JSON ('{self.EC2_DEFAULT_APP_PORTS_JSON}'). Using default empty list.")
            return []

    # Model configuration for .env file
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding='utf-8', extra='ignore')

settings = Settings()
