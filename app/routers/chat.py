from fastapi import APIRouter, HTTPException, Body, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from typing import AsyncGenerator, List, Dict, Any
import time
import uuid
import json
import asyncio

from app.core.schemas import (
    ChatCompletionRequest, ChatCompletionResponse, ChatMessage, Choice, Usage,
    ChatCompletionStreamResponse, ChatCompletionStreamChoice, ChoiceDelta,
    AWSCredentials # Added import
)
from app.core.config import settings
from app.core.logging_config import get_logger
from openai import AzureOpenAI, APIError

# Import orchestrator functions
from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment
)

logger = get_logger(__name__)

router = APIRouter(
    prefix="/v1/chat",
    tags=["Chat Completions (OpenAI Compatible)"]
)

client = None
if settings.AZURE_OPENAI_API_KEY and settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_API_VERSION:
    client = AzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )

@router.post("/completions", response_model=None)
async def create_chat_completion(request: ChatCompletionRequest = Body(...)) -> Any: # Return type can be StreamingResponse or JSONResponse
    """
    Creates a model response for the given chat conversation.
    If github_repo_url is provided, it triggers a deployment flow.
    Otherwise, it acts as a standard OpenAI compatible chat completion endpoint.
    """
    request_id = f"chatcmpl-{uuid.uuid4()}"
    created_timestamp = int(time.time())

    logger.info(f"Received request. Request ID: {request_id}, Stream: {request.stream}, Mode: {request.deployment_mode}, Repo: {request.github_repo_url}")

    # Check if it's a deployment request
    if request.github_repo_url:
        logger.info(f"Deployment requested for {request.github_repo_url} with mode: {request.deployment_mode} in namespace {request.target_namespace}")

        # Ensure target_namespace is provided (it has a default in schema, so it will always be there)
        if not request.target_namespace: # Should not happen due to default
            logger.error("Target namespace is missing for deployment.")
            raise HTTPException(status_code=400, detail="Target namespace is required for deployment.")

        deployment_response: Dict[str, Any] = {}
        try:
            if request.deployment_mode == "local":
                deployment_response = await handle_local_deployment(
                    repo_url=request.github_repo_url,
                    namespace=request.target_namespace,
                    chat_request=request
                )
            elif request.deployment_mode == "cloud-local":
                if not request.aws_credentials:
                    logger.error("AWS credentials required for cloud-local mode but not provided.")
                    raise HTTPException(status_code=400, detail="AWS credentials required for cloud-local deployment mode.")
                deployment_response = await handle_cloud_local_deployment(
                    repo_url=request.github_repo_url,
                    namespace=request.target_namespace,
                    aws_creds=request.aws_credentials,
                    chat_request=request
                )
            elif request.deployment_mode == "cloud-hosted":
                if not request.aws_credentials:
                    logger.error("AWS credentials required for cloud-hosted mode but not provided.")
                    raise HTTPException(status_code=400, detail="AWS credentials required for cloud-hosted deployment mode.")
                deployment_response = await handle_cloud_hosted_deployment(
                    repo_url=request.github_repo_url,
                    namespace=request.target_namespace,
                    aws_creds=request.aws_credentials,
                    chat_request=request
                )
            else:
                logger.error(f"Invalid deployment mode: {request.deployment_mode}")
                raise HTTPException(status_code=400, detail=f"Invalid deployment mode: {request.deployment_mode}")

            # For now, deployment responses are simple JSON. Streaming for deployments TBD.
            # The orchestrator stubs currently return a dict.
            return JSONResponse(content=deployment_response)

        except HTTPException as http_exc: # Re-raise HTTP exceptions from checks
            raise http_exc
        except Exception as e:
            logger.error(f"Error during deployment orchestration for Request ID {request_id}: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error during deployment: {str(e)}")

    # Standard Azure OpenAI chat logic if not a deployment request
    if not client:
        logger.error("Azure OpenAI client is not configured. Missing API key, endpoint, or version.")
        raise HTTPException(status_code=500, detail="Azure OpenAI client is not configured. Missing API key, endpoint, or version.")

    if not settings.AZURE_OPENAI_DEPLOYMENT:
        logger.error("Azure OpenAI deployment name is not configured.")
        raise HTTPException(status_code=500, detail="Azure OpenAI deployment name is not configured.")

    formatted_messages = [msg.model_dump(exclude_none=True) for msg in request.messages]

    try:
        if request.stream:
            async def stream_generator() -> AsyncGenerator[str, None]:
                try:
                    stream = await asyncio.to_thread(
                        client.chat.completions.create,
                        model=settings.AZURE_OPENAI_DEPLOYMENT,
                        messages=formatted_messages,
                        temperature=request.temperature,
                        top_p=request.top_p,
                        n=request.n,
                        stream=True,
                        stop=request.stop,
                        max_tokens=request.max_tokens,
                        presence_penalty=request.presence_penalty,
                        frequency_penalty=request.frequency_penalty,
                        user=request.user
                    )
                    async for chunk in stream:
                        if not chunk.choices:
                            continue

                        delta_content = chunk.choices[0].delta.content
                        delta_role = chunk.choices[0].delta.role
                        finish_reason = chunk.choices[0].finish_reason
                        choice_delta = ChoiceDelta(role=delta_role if delta_role else None, content=delta_content if delta_content else None)
                        stream_choice = ChatCompletionStreamChoice(index=chunk.choices[0].index, delta=choice_delta, finish_reason=finish_reason)
                        stream_response = ChatCompletionStreamResponse(id=request_id, created=created_timestamp, model=chunk.model or settings.AZURE_OPENAI_DEPLOYMENT, choices=[stream_choice])
                        yield f"data: {stream_response.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
                except APIError as e:
                    logger.error(f"Azure OpenAI API Error during stream for Request ID {request_id}: Status {e.status_code} - {e.message}", exc_info=True)
                    error_content = {"error": {"message": f"Azure OpenAI API Error: {e.message}", "type": "azure_openai_error", "code": e.status_code}}
                    stream_error_response = ChatCompletionStreamResponse(id=request_id, created=created_timestamp, model=settings.AZURE_OPENAI_DEPLOYMENT, choices=[ChatCompletionStreamChoice(index=0, delta=ChoiceDelta(content=json.dumps(error_content)), finish_reason="error")])
                    yield f"data: {stream_error_response.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    logger.error(f"Unexpected error during stream for Request ID {request_id}: {str(e)}", exc_info=True)
                    error_content = {"error": {"message": f"Unexpected stream error: {str(e)}", "type": "internal_error"}}
                    stream_error_response = ChatCompletionStreamResponse(id=request_id, created=created_timestamp, model=settings.AZURE_OPENAI_DEPLOYMENT, choices=[ChatCompletionStreamChoice(index=0, delta=ChoiceDelta(content=json.dumps(error_content)), finish_reason="error")])
                    yield f"data: {stream_error_response.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            completion = client.chat.completions.create(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                messages=formatted_messages,
                temperature=request.temperature,
                top_p=request.top_p,
                n=request.n,
                stream=False,
                stop=request.stop,
                max_tokens=request.max_tokens,
                presence_penalty=request.presence_penalty,
                frequency_penalty=request.frequency_penalty,
                user=request.user
            )
            response_choices: List[Choice] = []
            for choice_data in completion.choices:
                response_message = ChatMessage(role=choice_data.message.role, content=choice_data.message.content)
                response_choices.append(Choice(index=choice_data.index, message=response_message, finish_reason=choice_data.finish_reason))
            response_usage = Usage(prompt_tokens=completion.usage.prompt_tokens, completion_tokens=completion.usage.completion_tokens, total_tokens=completion.usage.total_tokens)
            return ChatCompletionResponse(id=completion.id, created=completion.created, model=completion.model, choices=response_choices, usage=response_usage)
    except APIError as e:
        logger.error(f"Azure OpenAI API Error for Request ID {request_id}: Status {e.status_code} - {e.message}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=f"Azure OpenAI API Error: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error in chat completion for Request ID {request_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
