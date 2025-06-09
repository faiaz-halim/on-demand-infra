from fastapi import APIRouter, HTTPException, Body, Depends
from fastapi.responses import StreamingResponse
from typing import AsyncGenerator, List
import time
import uuid
import json # Keep for streaming if model_dump_json is used, otherwise can remove if not directly used.
import asyncio # Keep for streaming

from app.core.schemas import (
    ChatCompletionRequest, ChatCompletionResponse, ChatMessage, Choice, Usage,
    ChatCompletionStreamResponse, ChatCompletionStreamChoice, ChoiceDelta
)
from app.core.config import settings
from app.core.logging_config import get_logger # Import get_logger
from openai import AzureOpenAI, APIError

logger = get_logger(__name__) # Create a logger for this module

router = APIRouter(
    prefix="/v1/chat",
    tags=["Chat Completions (OpenAI Compatible)"]
)

# Initialize AzureOpenAI client
# This will be None if keys are not set, handled in endpoint
client = None
if settings.AZURE_OPENAI_API_KEY and settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_API_VERSION:
    client = AzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )

@router.post("/completions", response_model=None) # response_model handled by StreamingResponse or direct JSONResponse
async def create_chat_completion(request: ChatCompletionRequest = Body(...)):
    """
    Creates a model response for the given chat conversation.
    Compatible with OpenAI's /v1/chat/completions endpoint.
    """
    if not client:
        # This log might be useful before raising the HTTP Exception
        logger.error("Azure OpenAI client is not configured. Missing API key, endpoint, or version.")
        raise HTTPException(status_code=500, detail="Azure OpenAI client is not configured. Missing API key, endpoint, or version.")

    if not settings.AZURE_OPENAI_DEPLOYMENT:
        logger.error("Azure OpenAI deployment name is not configured.")
        raise HTTPException(status_code=500, detail="Azure OpenAI deployment name is not configured.")

    request_id = f"chatcmpl-{uuid.uuid4()}"
    created_timestamp = int(time.time())

    logger.info(f"Received chat completion request. Request ID: {request_id}, Stream: {request.stream}")

    # Map Pydantic models to dicts for the OpenAI client
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
                    async for chunk in stream: # openai client stream is already async iterable
                        if not chunk.choices:
                            continue

                        delta_content = chunk.choices[0].delta.content
                        delta_role = chunk.choices[0].delta.role
                        finish_reason = chunk.choices[0].finish_reason

                        choice_delta = ChoiceDelta(role=delta_role if delta_role else None, content=delta_content if delta_content else None)

                        stream_choice = ChatCompletionStreamChoice(
                            index=chunk.choices[0].index,
                            delta=choice_delta,
                            finish_reason=finish_reason
                        )

                        stream_response = ChatCompletionStreamResponse(
                            id=request_id,
                            created=created_timestamp,
                            model=chunk.model or settings.AZURE_OPENAI_DEPLOYMENT,
                            choices=[stream_choice]
                        )
                        yield f"data: {stream_response.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
                except APIError as e:
                    logger.error(f"Azure OpenAI API Error during stream for Request ID {request_id}: Status {e.status_code} - {e.message}", exc_info=True)
                    error_response = ChatCompletionStreamResponse(
                        id=request_id,
                        created=created_timestamp,
                        model=settings.AZURE_OPENAI_DEPLOYMENT,
                        choices=[ChatCompletionStreamChoice(index=0, delta=ChoiceDelta(content=f"Error: {str(e)}"), finish_reason="error")]
                    )
                    yield f"data: {error_response.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    logger.error(f"Unexpected error during stream for Request ID {request_id}: {str(e)}", exc_info=True)
                    error_response = ChatCompletionStreamResponse(
                        id=request_id,
                        created=created_timestamp,
                        model=settings.AZURE_OPENAI_DEPLOYMENT,
                        choices=[ChatCompletionStreamChoice(index=0, delta=ChoiceDelta(content=f"Unexpected stream error: {str(e)}"), finish_reason="error")]
                    )
                    yield f"data: {error_response.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            # Non-streaming response
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
                response_message = ChatMessage(
                    role=choice_data.message.role,
                    content=choice_data.message.content
                )
                response_choices.append(
                    Choice(index=choice_data.index, message=response_message, finish_reason=choice_data.finish_reason)
                )

            response_usage = Usage(
                prompt_tokens=completion.usage.prompt_tokens,
                completion_tokens=completion.usage.completion_tokens,
                total_tokens=completion.usage.total_tokens
            )

            return ChatCompletionResponse(
                id=completion.id,
                created=completion.created,
                model=completion.model,
                choices=response_choices,
                usage=response_usage
            )
    except APIError as e:
        logger.error(f"Azure OpenAI API Error for Request ID {request_id}: Status {e.status_code} - {e.message}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=f"Azure OpenAI API Error: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error in chat completion for Request ID {request_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
