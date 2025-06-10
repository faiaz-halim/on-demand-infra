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
