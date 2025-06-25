import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class AzureOpenAIConfig:
    API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
    ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
    DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    API_VERSION = "2024-12-01-preview"  # Use a stable API version
