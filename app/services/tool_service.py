import logging
import asyncio
from typing import Dict, Callable, Any, Optional, List

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

TOOL_REGISTRY: Dict[str, Callable[..., Any]] = {}
TOOL_DEFINITIONS: List[Dict[str, Any]] = [] # Stores OpenAI ChatCompletionToolParam compatible dicts

def register_tool(name: str, func: Callable[..., Any], schema: Dict[str, Any]):
    """
    Registers a tool (function) and its schema.

    Args:
        name: The name of the tool, matching the function name in the schema.
        func: The actual callable function to execute for this tool.
        schema: The OpenAI function schema (parameters, description).
                Example: {"name": "get_current_weather", "description": "...", "parameters": {...}}
    """
    if name in TOOL_REGISTRY:
        logger.warning(f"Tool '{name}' is being re-registered. Overwriting existing tool.")

    TOOL_REGISTRY[name] = func

    # Check if tool with this name already exists in definitions to avoid duplicates
    existing_def_index = -1
    for i, tool_def in enumerate(TOOL_DEFINITIONS):
        if tool_def.get("function", {}).get("name") == name:
            existing_def_index = i
            break

    new_tool_definition = {"type": "function", "function": schema}
    if existing_def_index != -1:
        TOOL_DEFINITIONS[existing_def_index] = new_tool_definition
        logger.info(f"Tool '{name}' definition updated.")
    else:
        TOOL_DEFINITIONS.append(new_tool_definition)
        logger.info(f"Tool '{name}' registered with schema: {schema}")


async def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a registered tool by its name with the given arguments.

    Args:
        tool_name: The name of the tool to execute.
        arguments: A dictionary of arguments to pass to the tool function.

    Returns:
        A dictionary containing the status of the execution ("success" or "error"),
        and either "result" or "error_message".
    """
    logger.info(f"Attempting to execute tool '{tool_name}' with arguments: {arguments}")

    if tool_name not in TOOL_REGISTRY:
        logger.error(f"Tool '{tool_name}' not found in registry.")
        return {"status": "error", "error_message": f"Tool '{tool_name}' not found."}

    func = TOOL_REGISTRY[tool_name]

    try:
        if asyncio.iscoroutinefunction(func):
            result_content = await func(**arguments)
        else:
            # For synchronous tools, run in a thread to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            result_content = await loop.run_in_executor(None, lambda: func(**arguments))

        logger.info(f"Tool '{tool_name}' executed successfully. Result type: {type(result_content)}")
        # Ensure result_content is JSON serializable if it's directly passed.
        # The LLM expects a string content for the tool message.
        # So, the caller of execute_tool will typically json.dumps this dict.
        return {"status": "success", "result": result_content}
    except Exception as e:
        logger.exception(f"Error executing tool '{tool_name}' with arguments {arguments}: {e}")
        return {"status": "error", "error_message": str(e)}

async def get_tool_response(tool_name: str, query: str, context: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a tool call to external services (Context7 MCP or web search)
    to fetch documentation and best practices.

    Args:
        tool_name: 'context7_search' or 'web_search'
        query: Search query string
        context: Additional context for the search

    Returns:
        Dictionary with search results
    """
    # Placeholder implementation - integrate with actual APIs
    logger.info(f"Executing tool: {tool_name} with query: '{query}'")

    # Simulate API call delay
    await asyncio.sleep(1)

    return {
        "tool_name": tool_name,
        "results": [
            {
                "source": "https://docs.aws.amazon.com",
                "content_snippet": f"Best practices for {query} from AWS documentation"
            },
            {
                "source": "https://registry.terraform.io",
                "content_snippet": f"Terraform documentation for {query}"
            }
        ]
    }

# Register the tool call function
get_tool_response_schema = {
    "name": "get_tool_response",
    "description": "Search for information using Context7 MCP or web search",
    "parameters": {
        "type": "object",
        "properties": {
            "tool_name": {
                "type": "string",
                "enum": ["context7_search", "web_search"],
                "description": "The tool to use for search"
            },
            "query": {
                "type": "string",
                "description": "Search query string"
            },
            "context": {
                "type": "string",
                "description": "Additional context for the search"
            }
        },
        "required": ["tool_name", "query"]
    }
}

register_tool("get_tool_response", get_tool_response, get_tool_response_schema)

# Example (for potential direct testing or later use):
# async def example_tool_function(location: str, unit: str = "celsius"):
# """Example tool to get current weather."""
#     return {"temperature": "22", "unit": unit, "location": location, "forecast": "sunny"}

# def register_example_tool():
#     example_schema = {
#         "name": "get_current_weather",
#         "description": "Get the current weather in a given location",
#         "parameters": {
#             "type": "object",
#             "properties": {
#                 "location": {"type": "string", "description": "The city and state, e.g. San Francisco, CA"},
#                 "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
#             },
#             "required": ["location"],
#         },
#     }
#     register_tool("get_current_weather", example_tool_function, example_schema)

# if __name__ == '__main__':
#     # This would typically be called at application startup
#     register_example_tool()
#     print(f"Registered tools: {TOOL_DEFINITIONS}")

#     async def main_test():
#         # Simulate LLM asking to call a tool
#         args = {"location": "Boston, MA", "unit": "celsius"}
#         output = await execute_tool("get_current_weather", args)
#         print(f"\nOutput of get_current_weather: {output}")

#         args_nonexistent = {"param": "value"}
#         output_nonexistent = await execute_tool("non_existent_tool", args_nonexistent)
#         print(f"\nOutput of non_existent_tool: {output_nonexistent}")

#     asyncio.run(main_test())
