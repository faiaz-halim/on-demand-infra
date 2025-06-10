import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import logging

# Adjust import path as necessary
from app.tools.search_tools import web_search

# Configure basic logging for test visibility if service logs extensively
logging.basicConfig(level=logging.DEBUG) # Set to DEBUG to see info logs from the service if needed
logger = logging.getLogger(__name__)

class TestSearchTools(unittest.IsolatedAsyncioTestCase):

    @patch('app.tools.search_tools.DDGS')
    async def test_web_search_success(self, mock_ddgs_constructor):
        logger.info("Testing web_search_success...")
        mock_ddgs_instance = mock_ddgs_constructor.return_value
        sample_results = [
            {'title': 'Test Title 1', 'href': 'http://example.com/1', 'body': 'Test snippet 1...'},
            {'title': 'Test Title 2', 'href': 'http://example.com/2', 'body': 'Test snippet 2...'}
        ]
        mock_ddgs_instance.text.return_value = sample_results

        query = "test query"
        num_results = 2
        result = await web_search(query, num_results=num_results)

        mock_ddgs_constructor.assert_called_once() # DDGS()
        mock_ddgs_instance.text.assert_called_once_with(query, max_results=num_results)

        self.assertIn("summary", result)
        self.assertTrue(result["summary"].startswith(f"Found {len(sample_results)} results"))
        self.assertIsInstance(result["results"], list)
        self.assertEqual(len(result["results"]), len(sample_results))

        self.assertEqual(result["results"][0]["title"], sample_results[0]["title"])
        self.assertEqual(result["results"][0]["snippet"], sample_results[0]["body"])
        self.assertEqual(result["results"][0]["url"], sample_results[0]["href"])
        logger.info("test_web_search_success passed.")

    @patch('app.tools.search_tools.DDGS')
    async def test_web_search_no_results(self, mock_ddgs_constructor):
        logger.info("Testing web_search_no_results...")
        mock_ddgs_instance = mock_ddgs_constructor.return_value
        mock_ddgs_instance.text.return_value = [] # Simulate no results

        query = "query with no results"
        result = await web_search(query)

        mock_ddgs_instance.text.assert_called_once_with(query, max_results=5) # Default num_results
        self.assertEqual(result["summary"], f"No results found for '{query}'.")
        self.assertEqual(len(result["results"]), 0)
        logger.info("test_web_search_no_results passed.")

    @patch('app.tools.search_tools.DDGS')
    async def test_web_search_ddgs_raises_exception(self, mock_ddgs_constructor):
        logger.info("Testing web_search_ddgs_raises_exception...")
        mock_ddgs_instance = mock_ddgs_constructor.return_value
        mock_ddgs_instance.text.side_effect = Exception("DDGS API error")

        query = "failing query"
        result = await web_search(query)

        mock_ddgs_instance.text.assert_called_once_with(query, max_results=5)
        self.assertIn("Error performing search.", result["summary"])
        self.assertEqual(len(result["results"]), 0)
        self.assertEqual(result["error"], "DDGS API error")
        logger.info("test_web_search_ddgs_raises_exception passed.")

    @patch('app.tools.search_tools.DDGS')
    async def test_web_search_uses_default_num_results(self, mock_ddgs_constructor):
        logger.info("Testing web_search_uses_default_num_results...")
        mock_ddgs_instance = mock_ddgs_constructor.return_value
        # Return 5 results to match default, or any number to just check the call arg
        mock_ddgs_instance.text.return_value = [{'title': 'T', 'href': 'U', 'body': 'S'}] * 3

        query = "test query for default results"
        await web_search(query) # num_results not specified, should use default

        mock_ddgs_instance.text.assert_called_once_with(query, max_results=5) # Default is 5 in function signature
        logger.info("test_web_search_uses_default_num_results passed.")

    @patch('app.tools.search_tools.DDGS')
    async def test_web_search_result_formatting(self, mock_ddgs_constructor):
        logger.info("Testing web_search_result_formatting for missing keys...")
        mock_ddgs_instance = mock_ddgs_constructor.return_value
        # Result missing 'title', another missing 'href', one missing 'body'
        sample_results = [
            {'href': 'http://example.com/1', 'body': 'Test snippet 1...'},
            {'title': 'Test Title 2', 'body': 'Test snippet 2...'},
            {'title': 'Test Title 3', 'href': 'http://example.com/3'},
            {} # Empty dict
        ]
        mock_ddgs_instance.text.return_value = sample_results

        query = "test query formatting"
        result = await web_search(query, num_results=len(sample_results))

        self.assertEqual(len(result["results"]), len(sample_results))
        self.assertEqual(result["results"][0]["title"], "No title available")
        self.assertEqual(result["results"][0]["url"], "http://example.com/1")
        self.assertEqual(result["results"][1]["url"], "#")
        self.assertEqual(result["results"][2]["snippet"], "No snippet available")
        self.assertEqual(result["results"][3]["title"], "No title available")
        self.assertEqual(result["results"][3]["url"], "#")
        self.assertEqual(result["results"][3]["snippet"], "No snippet available")
        logger.info("test_web_search_result_formatting passed.")


if __name__ == '__main__':
    unittest.main()
