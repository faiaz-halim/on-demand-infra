from fastapi import FastAPI
from app.routers import chat
from app.core.logging_config import logger as app_logger # Use the main logger
from app.core.config import settings # To log settings at startup if desired

# Tool registration imports
from app.services.tool_service import register_tool
from app.tools.search_tools import web_search, WEB_SEARCH_TOOL_SCHEMA

app = FastAPI(
    title="MCP Server",
    version="0.1.0",
    description="Model Context Protocol Server for automated infrastructure deployment."
)

@app.on_event("startup")
async def startup_event():
    app_logger.info(f"MCP Server starting up. Log level: {settings.LOG_LEVEL}")
    if not settings.AZURE_OPENAI_API_KEY or not settings.AZURE_OPENAI_ENDPOINT:
        app_logger.warning("Azure OpenAI API Key or Endpoint is not configured.")
    else:
        app_logger.info("Azure OpenAI client configured.")

    # Register tools
    app_logger.info("Registering tools...")
    try:
        register_tool("web_search", web_search, WEB_SEARCH_TOOL_SCHEMA)
        app_logger.info("Tools registered successfully.")
    except Exception as e:
        app_logger.exception(f"An error occurred during tool registration: {e}")


@app.get("/health", tags=["General"])
async def health_check():
    """Perform a health check."""
    app_logger.debug("Health check endpoint called.")
    return {"status": "ok", "message": "MCP Server is healthy"}

app.include_router(chat.router)

if __name__ == "__main__":
    import uvicorn
    # Note: Tool registration via @app.on_event("startup") works with `uvicorn app.main:app`.
    # If running this file directly (`python app/main.py`), Uvicorn might not pick up lifespan events
    # unless configured to do so or if the app instance is passed in a specific way.
    # The standard way to run FastAPI with Uvicorn (`uvicorn app.main:app ...`) handles lifespan events.
    uvicorn.run(app, host="0.0.0.0", port=8000)
