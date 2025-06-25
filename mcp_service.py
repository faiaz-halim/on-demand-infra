import requests
from typing import Dict, Any
from .mcp_models import ToolCallRequestModel, ToolCallResponseModel

class MCPService:
    """Service for interacting with MCP servers"""

    def __init__(self):
        self.base_urls = {
            "context7": "https://context7.up.railway.app"
        }

    def call_tool(self, request: ToolCallRequestModel) -> ToolCallResponseModel:
        """Call a tool on an MCP server"""
        try:
            # Get base URL for the server
            base_url = self.base_urls.get(request.server_name)
            if not base_url:
                return ToolCallResponseModel(
                    success=False,
                    error=f"Unknown server: {request.server_name}"
                )

            # Construct full URL
            url = f"{base_url}/tools/{request.tool_name}"

            # Make the request
            response = requests.post(
                url,
                json=request.arguments,
                headers={"Content-Type": "application/json"}
            )

            # Handle response
            if response.status_code == 200:
                return ToolCallResponseModel(
                    success=True,
                    result=response.json()
                )
            else:
                return ToolCallResponseModel(
                    success=False,
                    error=f"Server returned status {response.status_code}",
                    logs=[response.text]
                )

        except Exception as e:
            return ToolCallResponseModel(
                success=False,
                error=str(e)
            )

    def call_context7_tool(self, tool_name: str, arguments: Dict[str, Any]) -> ToolCallResponseModel:
        """Convenience method for calling Context7 tools"""
        return self.call_tool(ToolCallRequestModel(
            server_name="context7",
            tool_name=tool_name,
            arguments=arguments
        ))
