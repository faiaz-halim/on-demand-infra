import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import logging

# Assuming app.services.tool_service path
from app.services import tool_service
from app.services.tool_service import (
    register_tool,
    execute_tool,
    TOOL_REGISTRY,
    TOOL_DEFINITIONS
)

# Configure basic logging for test visibility if service logs extensively
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class TestToolService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Clear registry and definitions before each test for isolation
        TOOL_REGISTRY.clear()
        TOOL_DEFINITIONS.clear()

    def test_register_tool(self):
        logger.info("Testing register_tool...")
        mock_async_func = AsyncMock(name="mock_async_func")
        mock_sync_func = MagicMock(name="mock_sync_func")

        schema1 = {"name": "dummy_async_tool", "description": "Async tool", "parameters": {}}
        schema2 = {"name": "dummy_sync_tool", "description": "Sync tool", "parameters": {}}

        register_tool("dummy_async_tool", mock_async_func, schema1)
        self.assertIn("dummy_async_tool", TOOL_REGISTRY)
        self.assertEqual(TOOL_REGISTRY["dummy_async_tool"], mock_async_func)
        self.assertEqual(len(TOOL_DEFINITIONS), 1)
        self.assertEqual(TOOL_DEFINITIONS[0], {"type": "function", "function": schema1})

        register_tool("dummy_sync_tool", mock_sync_func, schema2)
        self.assertIn("dummy_sync_tool", TOOL_REGISTRY)
        self.assertEqual(TOOL_REGISTRY["dummy_sync_tool"], mock_sync_func)
        self.assertEqual(len(TOOL_DEFINITIONS), 2)
        self.assertEqual(TOOL_DEFINITIONS[1], {"type": "function", "function": schema2})

        # Test re-registering (should update)
        mock_async_func_updated = AsyncMock(name="mock_async_func_updated")
        schema1_updated = {"name": "dummy_async_tool", "description": "Updated Async tool", "parameters": {}}
        register_tool("dummy_async_tool", mock_async_func_updated, schema1_updated)
        self.assertEqual(TOOL_REGISTRY["dummy_async_tool"], mock_async_func_updated)
        self.assertEqual(len(TOOL_DEFINITIONS), 2) # Count should remain same
        # Check if the definition was updated
        updated_def = next(d for d in TOOL_DEFINITIONS if d["function"]["name"] == "dummy_async_tool")
        self.assertEqual(updated_def["function"]["description"], "Updated Async tool")
        logger.info("test_register_tool passed.")


    async def test_execute_tool_success_async(self):
        logger.info("Testing execute_tool_success_async...")
        mock_async_tool = AsyncMock(return_value={"data": "async success"})
        schema = {"name": "test_async", "description": "Test async tool", "parameters": {}}
        register_tool("test_async", mock_async_tool, schema)

        arguments = {"arg1": "val1"}
        result = await execute_tool("test_async", arguments)

        mock_async_tool.assert_called_once_with(arg1="val1")
        self.assertEqual(result, {"status": "success", "result": {"data": "async success"}})
        logger.info("test_execute_tool_success_async passed.")

    async def test_execute_tool_success_sync(self):
        logger.info("Testing execute_tool_success_sync...")
        mock_sync_tool = MagicMock(return_value={"data": "sync success"})
        schema = {"name": "test_sync", "description": "Test sync tool", "parameters": {}}
        register_tool("test_sync", mock_sync_tool, schema)

        arguments = {"arg1": "val1"}
        # Patch asyncio.get_running_loop().run_in_executor for sync tool execution
        with patch('asyncio.get_running_loop') as mock_get_loop:
            mock_loop = MagicMock()
            mock_loop.run_in_executor.return_value = await asyncio.Future() # Create a future
            mock_loop.run_in_executor.return_value.set_result({"data": "sync success"}) # Set future result
            mock_get_loop.return_value = mock_loop

            result = await execute_tool("test_sync", arguments)

        mock_sync_tool.assert_called_once_with(arg1="val1")
        self.assertEqual(result, {"status": "success", "result": {"data": "sync success"}})
        logger.info("test_execute_tool_success_sync passed.")


    async def test_execute_tool_not_found(self):
        logger.info("Testing execute_tool_not_found...")
        result = await execute_tool("nonexistent_tool", {})
        self.assertEqual(result, {"status": "error", "error_message": "Tool 'nonexistent_tool' not found."})
        logger.info("test_execute_tool_not_found passed.")


    async def test_execute_tool_execution_exception_async(self):
        logger.info("Testing execute_tool_execution_exception_async...")
        mock_tool = AsyncMock(side_effect=ValueError("Async tool error"))
        schema = {"name": "error_tool_async", "description": "Test error tool", "parameters": {}}
        register_tool("error_tool_async", mock_tool, schema)

        result = await execute_tool("error_tool_async", {})

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_message"], "Async tool error")
        logger.info("test_execute_tool_execution_exception_async passed.")

    async def test_execute_tool_execution_exception_sync(self):
        logger.info("Testing execute_tool_execution_exception_sync...")
        mock_tool = MagicMock(side_effect=TypeError("Sync tool type error"))
        schema = {"name": "error_tool_sync", "description": "Test error tool sync", "parameters": {}}
        register_tool("error_tool_sync", mock_tool, schema)

        with patch('asyncio.get_running_loop') as mock_get_loop:
            mock_loop = MagicMock()
            # Make run_in_executor raise the exception when the lambda is called
            async def mock_run_in_executor(executor, func_call):
                raise mock_tool.side_effect # pylint: disable=raising-bad-type
            mock_loop.run_in_executor = mock_run_in_executor
            mock_get_loop.return_value = mock_loop

            result = await execute_tool("error_tool_sync", {})

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_message"], "Sync tool type error")
        logger.info("test_execute_tool_execution_exception_sync passed.")


if __name__ == '__main__':
    unittest.main()
