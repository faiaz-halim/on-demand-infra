from typing import Dict, Any, List, Optional
from pydantic import BaseModel

class ToolCallRequestModel(BaseModel):
    """Model for making a tool call to an MCP server"""
    server_name: str
    tool_name: str
    arguments: Dict[str, Any]

class ToolCallResponseModel(BaseModel):
    """Model for the response from an MCP server tool call"""
    success: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    logs: Optional[List[str]] = None
