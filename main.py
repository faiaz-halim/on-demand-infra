import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from models import APIRequestModel
from ai_service import AIService
import time
import json
from exceptions import AppBaseError, InfrastructureProvisioningError, ApplicationBuildError, ConfigurationError, UserInputValidationError

# Configure structured logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("uvicorn")
logger.handlers.clear()

# Create JSON formatter
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        return json.dumps(log_record)

# Add handler with JSON formatter
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger.addHandler(handler)
logger.propagate = False

app = FastAPI()
ai_service = AIService()

@app.exception_handler(AppBaseError)
async def app_base_error_handler(request: Request, exc: AppBaseError):
    """Global exception handler for custom AppBaseError exceptions"""
    logger.error(f"Custom error occurred: {exc.message}", extra=exc.details)
    return JSONResponse(
        status_code=500,
        content={
            "error_type": exc.__class__.__name__,
            "message": exc.message,
            "details": exc.details
        }
    )

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware to log incoming requests"""
    logger.info("Incoming request",
                extra={"method": request.method, "path": request.url.path})

    try:
        response = await call_next(request)
    except Exception as e:
        logger.error("Request failed", exc_info=True)
        return JSONResponse(
            content={"error": "Internal server error"},
            status_code=500
        )

    logger.info("Request completed",
                extra={"status_code": response.status_code})
    return response

@app.get("/health")
def health_check():
    """Health check endpoint"""
    logger.info("Health check request received")
    return {"status": "ok"}

@app.post("/v1/chat/completions")
def chat_completions(request_data: APIRequestModel):
    """OpenAI-compatible endpoint for deployment requests"""
    # Extract and log parameters
    prompt = request_data.prompt
    github_url = request_data.github_url
    deployment_mode = request_data.deployment_mode
    aws_credentials = request_data.aws_credentials

    logger.info("Received deployment request",
                extra={
                    "prompt": prompt,
                    "github_url": github_url,
                    "deployment_mode": deployment_mode
                })

    # Use GitHubService to analyze repository
    from github_service import GitHubService
    github_service = GitHubService()
    analysis = github_service.analyze_repo(github_url)

    # Use AIService to generate code snippet with analysis context
    context = f"""
    Repository Analysis:
    - Has Dockerfile: {analysis.has_dockerfile}
    - Build commands: {analysis.build_commands}
    - Run commands: {analysis.run_commands}
    """
    code_snippet = ai_service.generate_code_snippet(prompt, context)

    # Log the analysis results
    logger.info("GitHub repository analysis completed", extra={
        "has_dockerfile": analysis.has_dockerfile,
        "build_commands": analysis.build_commands,
        "run_commands": analysis.run_commands
    })

    # Update response with extracted information
    response_data = {
        "id": "cmpl-12345",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "gpt-custom-model",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": f"Generated infrastructure code:\n{code_snippet}"
            },
            "finish_reason": "stop"
        }]
    }

    logger.info("Sending response", extra={"response": response_data})
    return response_data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)
