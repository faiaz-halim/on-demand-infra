from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # Azure OpenAI API settings
    AZURE_OPENAI_API_KEY: Optional[str] = None
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    AZURE_OPENAI_API_VERSION: Optional[str] = "2023-12-01-preview" # Or your default
    AZURE_OPENAI_DEPLOYMENT: Optional[str] = None # For chat model

    # Azure OpenAI Embedding settings
    AZURE_EMBEDDING_API_VERSION: Optional[str] = None # e.g., "2023-05-15"
    AZURE_EMBEDDING_DEPLOYMENT: Optional[str] = None # Deployment name for embedding model
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT: Optional[str] = None # Potentially duplicate/alias, clarify if needed, keeping for now

    # Langchain settings
    LANGCHAIN_API_KEY: Optional[str] = None
    LANGCHAIN_ENDPOINT: Optional[str] = None
    LANGCHAIN_PROJECT: Optional[str] = None
    LANGCHAIN_TRACING_V2: Optional[str] = "false" # Typically "true" or "false"

    # AWS settings
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: Optional[str] = None

    # Logging configuration
    LOG_LEVEL: str = "INFO"

    # Kind Cluster settings
    KIND_CLUSTER_NAME: str = "on-demand-infra"
    KIND_CALICO_MANIFEST_URL: Optional[str] = "https://raw.githubusercontent.com/projectcalico/calico/v3.28.0/manifests/calico.yaml"


    # Model configuration for .env file
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding='utf-8', extra='ignore')

settings = Settings()
