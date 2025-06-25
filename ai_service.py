import json
from openai import AzureOpenAI
from config import AzureOpenAIConfig
from models import APIRequestModel

class AIService:
    def __init__(self):
        """Initialize Azure OpenAI client using configuration"""
        self.deployment_name = AzureOpenAIConfig.DEPLOYMENT_NAME
        self.client = AzureOpenAI(
            api_key=AzureOpenAIConfig.API_KEY,
            api_version=AzureOpenAIConfig.API_VERSION,
            azure_endpoint=AzureOpenAIConfig.ENDPOINT
        )

    def get_intent(self, prompt: str) -> dict:
        """Extract deployment parameters from user prompt using Azure OpenAI"""
        from .security_utils import sanitize_shell_input

        # Sanitize the prompt input
        sanitized_prompt = sanitize_shell_input(prompt)
        if sanitized_prompt is None:
            return {"error": "Invalid input detected in prompt"}

        system_message = (
            "Extract deployment parameters from user requests. "
            "Return JSON with: github_url (string) and deployment_mode "
            "(one of: 'local', 'cloud-local', 'cloud-hosted'). "
            "Example: {'github_url': 'https://github.com/user/repo', 'deployment_mode': 'local'}"
        )

        response = self.client.chat.completions.create(
            model=self.deployment_name,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": sanitized_prompt}
            ],
            temperature=0.1
        )

        try:
            return json.loads(response.choices[0].message.content)
        except json.JSONDecodeError:
            return {"error": "Failed to parse intent"}

    def generate_code_snippet(self, prompt: str, context: str) -> str:
        from .security_utils import sanitize_shell_input

        # Sanitize the prompt input
        sanitized_prompt = sanitize_shell_input(prompt)
        if sanitized_prompt is None:
            return "Error: Invalid input detected in prompt"

        # Use the sanitized prompt
        context = f"{context}\nUser Prompt: {sanitized_prompt}"
        # Use the sanitized prompt
        sanitized_context = f"{context}\nUser Prompt: {sanitized_prompt}"

        # First, try to extract deployment parameters
        intent = self.get_intent(sanitized_prompt)
        # First, try to extract deployment parameters
        intent = self.get_intent(sanitized_prompt)

        # If intent extraction failed, use the fallback method with sanitized input
        if "error" in intent:
            # Fallback to the original method if intent extraction fails
            system_message = (
                "You are a DevOps engineer. Generate infrastructure as code "
                "based on the user request. Return only the code with no explanations. "
                "Use Terraform for cloud resources, Dockerfiles for containers, "
                "and Kubernetes manifests for orchestration."
            )

            response = self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": f"Request: {sanitized_prompt}\nContext: {sanitized_context}"}
                ],
                temperature=0.2
            )

            return response.choices[0].message.content
        else:
            # Use the DeploymentOrchestrator to generate artifacts
            from deployment_orchestrator import DeploymentOrchestrator

            orchestrator = DeploymentOrchestrator()
            app_name = "on-demand-app"
            image = "on-demand-image:latest"

            if intent["deployment_mode"] == "local":
                artifacts = orchestrator.generate_local_deployment(app_name, image)
            else:
                artifacts = orchestrator.generate_cloud_deployment(
                    app_name, image, "on-demand-cluster"
                )

            # Format the artifacts as a string
            result = []
            for artifact_type, content in artifacts.items():
                result.append(f"# {artifact_type.upper()} CONFIGURATION")
                result.append(content)
                result.append("")

            return "\n".join(result)
        """Generate infrastructure code using Azure OpenAI"""
        # First, try to extract deployment parameters
        intent = self.get_intent(prompt)

        if "error" in intent:
            # Fallback to the original method if intent extraction fails
            system_message = (
                "You are a DevOps engineer. Generate infrastructure as code "
                "based on the user request. Return only the code with no explanations. "
                "Use Terraform for cloud resources, Dockerfiles for containers, "
                "and Kubernetes manifests for orchestration."
            )

            response = self.client.chat.completions.create(
                model=self.deployment_name,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": f"Request: {prompt}\nContext: {context}"}
                ],
                temperature=0.2
            )

            return response.choices[0].message.content
        else:
            # Use the DeploymentOrchestrator to generate artifacts
            from deployment_orchestrator import DeploymentOrchestrator

            orchestrator = DeploymentOrchestrator()
            app_name = "on-demand-app"
            image = "on-demand-image:latest"

            if intent["deployment_mode"] == "local":
                artifacts = orchestrator.generate_local_deployment(app_name, image)
            else:
                artifacts = orchestrator.generate_cloud_deployment(
                    app_name, image, "on-demand-cluster"
                )

            # Format the artifacts as a string
            result = []
            for artifact_type, content in artifacts.items():
                result.append(f"# {artifact_type.upper()} CONFIGURATION")
                result.append(content)
                result.append("")

            return "\n".join(result)
