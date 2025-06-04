from fastapi import FastAPI

app = FastAPI(
    title="MCP Server",
    version="0.1.0",
    description="Meta-Code Platform Server for automated infrastructure deployment."
)

@app.get("/health", tags=["General"])
async def health_check():
    """Perform a health check."""
    return {"status": "ok", "message": "MCP Server is healthy"}

# Further routers will be included here later
# from .routers import some_router
# app.include_router(some_router.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
