from fastapi import FastAPI
from app.routers import chat
from app.core.logging_config import logger as app_logger # Use the main logger
from app.core.config import settings # To log settings at startup if desired

app = FastAPI(
    title="MCP Server",
    version="0.1.0",
    description="Meta-Code Platform Server for automated infrastructure deployment."
)

@app.on_event("startup")
async def startup_event():
    app_logger.info(f"MCP Server starting up. Log level: {settings.LOG_LEVEL}")
    if not settings.AZURE_OPENAI_API_KEY or not settings.AZURE_OPENAI_ENDPOINT:
        app_logger.warning("Azure OpenAI API Key or Endpoint is not configured.")
    else:
        app_logger.info("Azure OpenAI client configured.")
    # You can add more startup logging here, e.g., for other services

@app.get("/health", tags=["General"])
async def health_check():
    """Perform a health check."""
    app_logger.debug("Health check endpoint called.")
    return {"status": "ok", "message": "MCP Server is healthy"}

app.include_router(chat.router)

if __name__ == "__main__":
    import uvicorn
    # Uvicorn will use its own logging configuration by default for access logs.
    # Our application logs will go through the mcp_server_logger.
    # To customize Uvicorn's logging, you'd pass a log_config dictionary to uvicorn.run()
    # or use a separate uvicorn_log_config.json.
    # For now, we focus on application logging.
    uvicorn.run(app, host="0.0.0.0", port=8000)
