from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # Azure OpenAI API settings
    AZURE_OPENAI_API_KEY: Optional[str] = None
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    OPENAI_API_VERSION: str = "2023-12-01-preview" # Default or common version

    # Logging configuration
    LOG_LEVEL: str = "INFO"

    # Model configuration for .env file
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding='utf-8', extra='ignore')

settings = Settings()
