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
    AWSCredentials
)
from app.core.config import settings
from app.core.logging_config import get_logger
from openai import AzureOpenAI, APIError
from openai.types.chat import ChatCompletionMessageToolCall # For type hinting tool_calls

# Import orchestrator functions
from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment
)
# Import tool service components
from app.services.tool_service import TOOL_DEFINITIONS, execute_tool

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

MCP_SYSTEM_PROMPT = """You are an expert AI assistant for the Meta-Code Platform (MCP). Your goal is to help users automate infrastructure deployment and application setup.
You have access to several tools to assist you. When a user asks a question or requests an operation that could benefit from up-to-date information, technical documentation, best practices, or troubleshooting for specific errors, consider using the 'web_search' tool.
For example, if asked about "best practices for securing an S3 bucket with Terraform," or "how to resolve 'XYZ error' with Kubernetes," you should use the web_search tool to gather relevant information before formulating your response or plan.
When you use a tool, you will receive its output. Use this output to provide a comprehensive and accurate answer to the user.
If a user requests a deployment, guide them through the process, leveraging your knowledge and any information gathered from tools if needed for planning the deployment steps or generating configurations.
"""

@router.post("/completions", response_model=None)
async def create_chat_completion(request: ChatCompletionRequest = Body(...)) -> Any:
    request_id = f"chatcmpl-{uuid.uuid4()}"
    created_timestamp = int(time.time())

    logger.info(f"Received request. Request ID: {request_id}, Stream: {request.stream}, Mode: {request.deployment_mode}, Repo: {request.github_repo_url}")

    if request.github_repo_url:
        # ... (deployment logic - unchanged from previous version) ...
        logger.info(f"Deployment requested for {request.github_repo_url} with mode: {request.deployment_mode} in namespace {request.target_namespace}")
        if not request.target_namespace:
            logger.error("Target namespace is missing for deployment.")
            raise HTTPException(status_code=400, detail="Target namespace is required for deployment.")
        deployment_response: Dict[str, Any] = {}
        try:
            if request.deployment_mode == "local":
                deployment_response = await handle_local_deployment(request.github_repo_url, request.target_namespace, request)
            elif request.deployment_mode == "cloud-local":
                if not request.aws_credentials:
                    raise HTTPException(status_code=400, detail="AWS credentials required for cloud-local deployment mode.")
                deployment_response = await handle_cloud_local_deployment(request.github_repo_url, request.target_namespace, request.aws_credentials, request)
            elif request.deployment_mode == "cloud-hosted":
                if not request.aws_credentials:
                    raise HTTPException(status_code=400, detail="AWS credentials required for cloud-hosted deployment mode.")
                deployment_response = await handle_cloud_hosted_deployment(request.github_repo_url, request.target_namespace, request.aws_credentials, request)
            else:
                raise HTTPException(status_code=400, detail=f"Invalid deployment mode: {request.deployment_mode}")
            return JSONResponse(content=deployment_response)
        except HTTPException as http_exc:
            raise http_exc
        except Exception as e:
            logger.error(f"Error during deployment orchestration for Request ID {request_id}: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error during deployment: {str(e)}")

    if not client:
        raise HTTPException(status_code=500, detail="Azure OpenAI client is not configured.")
    if not settings.AZURE_OPENAI_DEPLOYMENT:
        raise HTTPException(status_code=500, detail="Azure OpenAI deployment name is not configured.")

    # Prepare messages for LLM, including the system prompt
    formatted_messages: List[Dict[str, Any]] = []
    has_system_prompt = any(msg.role == "system" for msg in request.messages)

    if not has_system_prompt:
        formatted_messages.append({"role": "system", "content": MCP_SYSTEM_PROMPT})
        logger.debug("Prepended default MCP system prompt.")

    for msg in request.messages:
        formatted_messages.append(msg.model_dump(exclude_none=True))

    try:
        if request.stream:
            logger.info("Streaming request: Tool calls will be ignored in this mode for now.")
            async def stream_generator() -> AsyncGenerator[str, None]:
                try:
                    stream_params = {
                        "model": settings.AZURE_OPENAI_DEPLOYMENT, "messages": formatted_messages, # type: ignore [arg-type]
                        "temperature": request.temperature, "top_p": request.top_p,
                        "n": request.n, "stream": True, "stop": request.stop,
                        "max_tokens": request.max_tokens, "presence_penalty": request.presence_penalty,
                        "frequency_penalty": request.frequency_penalty, "user": request.user
                    }
                    stream = await asyncio.to_thread(client.chat.completions.create, **stream_params)
                    async for chunk in stream:
                        if not chunk.choices: continue
                        delta = chunk.choices[0].delta
                        stream_choice = ChatCompletionStreamChoice(index=0, delta=ChoiceDelta(role=delta.role, content=delta.content), finish_reason=chunk.choices[0].finish_reason)
                        yield f"data: {ChatCompletionStreamResponse(id=request_id, created=created_timestamp, model=chunk.model or settings.AZURE_OPENAI_DEPLOYMENT, choices=[stream_choice]).model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
                except APIError as e:
                    error_content = {"error": {"message": f"Azure OpenAI API Error: {e.message}", "type": "azure_openai_error", "code": e.status_code}}
                    stream_error_response = ChatCompletionStreamResponse(id=request_id, created=created_timestamp, model=settings.AZURE_OPENAI_DEPLOYMENT, choices=[ChatCompletionStreamChoice(index=0, delta=ChoiceDelta(content=json.dumps(error_content)), finish_reason="error")])
                    yield f"data: {stream_error_response.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    error_content = {"error": {"message": f"Unexpected stream error: {str(e)}", "type": "internal_error"}}
                    stream_error_response = ChatCompletionStreamResponse(id=request_id, created=created_timestamp, model=settings.AZURE_OPENAI_DEPLOYMENT, choices=[ChatCompletionStreamChoice(index=0, delta=ChoiceDelta(content=json.dumps(error_content)), finish_reason="error")])
                    yield f"data: {stream_error_response.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"
            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            first_pass_params: Dict[str, Any] = { # type: ignore [no-redef]
                "model": settings.AZURE_OPENAI_DEPLOYMENT, "messages": formatted_messages, # type: ignore [arg-type]
                "temperature": request.temperature, "top_p": request.top_p,
                "n": request.n, "stream": False, "stop": request.stop,
                "max_tokens": request.max_tokens, "presence_penalty": request.presence_penalty,
                "frequency_penalty": request.frequency_penalty, "user": request.user
            }
            if TOOL_DEFINITIONS:
                first_pass_params["tools"] = TOOL_DEFINITIONS
                first_pass_params["tool_choice"] = "auto"

            logger.debug(f"First pass to LLM with params: {first_pass_params}")
            completion = client.chat.completions.create(**first_pass_params)

            response_message = completion.choices[0].message

            if response_message.tool_calls:
                logger.info(f"LLM requested tool calls: {response_message.tool_calls}")
                current_messages_for_llm: List[Dict[str, Any]] = list(formatted_messages)
                assistant_message_dict = response_message.model_dump(exclude_none=True)
                current_messages_for_llm.append(assistant_message_dict)

                for tool_call in response_message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_call_id = tool_call.id
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse arguments for tool {tool_name}: {tool_call.function.arguments}")
                        tool_output_content = {"status": "error", "error_message": f"Invalid arguments JSON: {tool_call.function.arguments}"}
                    else:
                        tool_output = await execute_tool(tool_name, arguments)
                        tool_output_content = tool_output

                    current_messages_for_llm.append({
                        "role": "tool", "tool_call_id": tool_call_id,
                        "name": tool_name, "content": json.dumps(tool_output_content)
                    })

                logger.debug(f"Second pass to LLM with history including tool results: {current_messages_for_llm}")
                completion = client.chat.completions.create(
                    model=settings.AZURE_OPENAI_DEPLOYMENT, messages=current_messages_for_llm, # type: ignore [arg-type]
                    temperature=request.temperature, top_p=request.top_p,
                    n=request.n, stream=False, stop=request.stop,
                    max_tokens=request.max_tokens, presence_penalty=request.presence_penalty,
                    frequency_penalty=request.frequency_penalty, user=request.user
                )
                response_message = completion.choices[0].message

            response_choices: List[Choice] = [
                Choice(index=0, message=ChatMessage(role=response_message.role or "assistant", content=response_message.content),
                       finish_reason=completion.choices[0].finish_reason)
            ]
            response_usage = Usage(
                prompt_tokens=completion.usage.prompt_tokens if completion.usage else 0,
                completion_tokens=completion.usage.completion_tokens if completion.usage else 0,
                total_tokens=completion.usage.total_tokens if completion.usage else 0
            )
            return ChatCompletionResponse(
                id=completion.id or request_id, created=completion.created or created_timestamp,
                model=completion.model or settings.AZURE_OPENAI_DEPLOYMENT,
                choices=response_choices, usage=response_usage
            )
    except APIError as e:
        logger.error(f"Azure OpenAI API Error for Request ID {request_id}: Status {e.status_code} - {e.message}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=f"Azure OpenAI API Error: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error in chat completion for Request ID {request_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
