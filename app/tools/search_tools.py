import logging
import asyncio
from typing import Dict, Any, List, Optional
from duckduckgo_search import DDGS # For v5.x.x

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

WEB_SEARCH_TOOL_SCHEMA = {
    "name": "web_search",
    "description": "Performs a web search using DuckDuckGo. Useful for finding recent information, technical documentation, best practices for software development, infrastructure setup, or troubleshooting specific error messages.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query to find information about."
            },
            "num_results": {
                "type": "integer",
                "description": "The desired number of search results to return. Defaults to 5.",
                "default": 5
            }
        },
        "required": ["query"]
    }
}

async def web_search(query: str, num_results: int = 5) -> Dict[str, Any]:
    """
    Performs a web search using DuckDuckGo for the given query.

    Args:
        query: The search query string.
        num_results: The maximum number of results to return.

    Returns:
        A dictionary containing a summary and a list of search results,
        or an error message if the search fails.
        Each result in the list is a dict with 'title', 'snippet', and 'url'.
    """
    logger.info(f"Performing web search for query: '{query}', num_results: {num_results}")

    try:
        loop = asyncio.get_running_loop()
        raw_results = await loop.run_in_executor(
            None,
            lambda: DDGS().text(query, max_results=num_results)
        )

        processed_results: List[Dict[str, str]] = []
        if raw_results:
            for res in raw_results:
                processed_results.append({
                    "title": res.get("title", "No title available"),
                    "snippet": res.get("body", "No snippet available"),
                    "url": res.get("href", "#")
                })

        if not processed_results:
            logger.info(f"No search results found for '{query}'.")
            return {"summary": f"No results found for '{query}'.", "results": []}

        summary = f"Found {len(processed_results)} results for '{query}'."
        logger.info(summary)
        return {"summary": summary, "results": processed_results}

    except Exception as e:
        logger.exception(f"Error during web search for query '{query}': {e}")
        return {"summary": "Error performing search.", "results": [], "error": str(e)}
