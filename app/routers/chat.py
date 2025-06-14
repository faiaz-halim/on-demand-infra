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
from openai.types.chat import ChatCompletionMessageToolCall

from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment,
    handle_cloud_local_decommission,
    handle_cloud_local_redeploy,
    handle_cloud_local_scale,
    handle_cloud_hosted_decommission,
    handle_cloud_hosted_redeploy # Added for cloud-hosted redeploy
)
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

    logger.info(f"Received request. Request ID: {request_id}, Action: {request.action}, Stream: {request.stream}, Mode: {request.deployment_mode}, Repo: {request.github_repo_url}, Instance ID: {request.instance_id}, Instance Name: {request.instance_name}")

    # Primary dispatch based on action
    if request.action == "deploy":
        if not request.github_repo_url:
            raise HTTPException(status_code=400, detail="GitHub repository URL ('github_repo_url') is required for 'deploy' action.")
        if not request.target_namespace: # Should have default
            raise HTTPException(status_code=400, detail="Target namespace ('target_namespace') is required for 'deploy' action.")

        logger.info(f"Deployment action for {request.github_repo_url} with mode: {request.deployment_mode} in namespace {request.target_namespace}, Instance Name: {request.instance_name}")
        response_data: Dict[str, Any] = {}
        try:
            if request.deployment_mode == "local":
                response_data = await handle_local_deployment(request.github_repo_url, request.target_namespace, request)
            elif request.deployment_mode == "cloud-local":
                if not request.aws_credentials:
                    raise HTTPException(status_code=400, detail="AWS credentials ('aws_credentials') are required for cloud-local deployment mode.")
                response_data = await handle_cloud_local_deployment(request.github_repo_url, request.target_namespace, request.aws_credentials, request)
            elif request.deployment_mode == "cloud-hosted":
                if not request.aws_credentials:
                    raise HTTPException(status_code=400, detail="AWS credentials ('aws_credentials') are required for cloud-hosted deployment mode.")
                response_data = await handle_cloud_hosted_deployment(request.github_repo_url, request.target_namespace, request.aws_credentials, request)
            else:
                raise HTTPException(status_code=400, detail=f"Invalid deployment mode for deploy action: {request.deployment_mode}")
            return JSONResponse(content=response_data)
        except HTTPException as http_exc:
            raise http_exc
        except Exception as e:
            logger.error(f"Error during 'deploy' action for Request ID {request_id}: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error during 'deploy' action: {str(e)}")

    elif request.action == "decommission":
        if not request.instance_id:
            raise HTTPException(status_code=400, detail="Instance ID ('instance_id') is required for 'decommission' action.")

        logger.info(f"Decommission action requested for instance: {request.instance_id} (Instance Name: {request.instance_name}) in mode: {request.deployment_mode}")
        response_data: Dict[str, Any] = {}
        try:
            if request.deployment_mode == "cloud-local":
                if not request.aws_credentials:
                    raise HTTPException(status_code=400, detail="AWS credentials ('aws_credentials') are required for 'decommission' action in cloud-local mode.")
                response_data = await handle_cloud_local_decommission(request.instance_id, request.aws_credentials, request)
            elif request.deployment_mode == "cloud-hosted":
                if not request.aws_credentials:
                    raise HTTPException(status_code=400, detail="AWS credentials are required for cloud-hosted decommission action.")
                logger.info(f"Cloud-hosted decommission requested for EKS cluster (instance_id): {request.instance_id}")
                response_data = await handle_cloud_hosted_decommission(
                    cluster_name=request.instance_id, # instance_id is the cluster_name for cloud-hosted
                    aws_creds=request.aws_credentials,
                    chat_request=request
                )
            else:
                raise HTTPException(status_code=400, detail=f"Decommission not supported for deployment mode: {request.deployment_mode} with instance_id")
            return JSONResponse(content=response_data)
        except HTTPException as http_exc:
            raise http_exc
        except Exception as e:
            logger.error(f"Error during 'decommission' action for Request ID {request_id}, Instance ID {request.instance_id}: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Error during 'decommission' action: {str(e)}")

    elif request.action == "redeploy":
        if request.deployment_mode == "cloud-local":
            if not request.instance_id:
                raise HTTPException(status_code=400, detail="Instance ID ('instance_id') is required for 'redeploy' action.")
            if not request.public_ip:
                raise HTTPException(status_code=400, detail="Public IP ('public_ip') is required for 'redeploy' action on cloud-local instance.")
            if not request.ec2_key_name:
                 raise HTTPException(status_code=400, detail="EC2 key name ('ec2_key_name') is required for 'redeploy' action on cloud-local instance.")
            if not request.github_repo_url:
                raise HTTPException(status_code=400, detail="GitHub repository URL ('github_repo_url') for the new version is required for 'redeploy' action.")
            if not request.target_namespace:
                raise HTTPException(status_code=400, detail="Target Kubernetes namespace ('target_namespace') is required for 'redeploy' action.")

            logger.info(f"Redeploy action requested for instance: {request.instance_id} (Instance Name: {request.instance_name}, IP: {request.public_ip}), new repo source: {request.github_repo_url}")
            response_data: Dict[str, Any] = {}
            try:
                if not request.aws_credentials:
                    logger.warning("AWS credentials not provided for cloud-local redeploy, proceeding but some operations might fail if they require AWS API access.")

                response_data = await handle_cloud_local_redeploy(
                    instance_id=request.instance_id, public_ip=request.public_ip,
                    ec2_key_name=request.ec2_key_name, repo_url=request.github_repo_url,
                    namespace=request.target_namespace, aws_creds=request.aws_credentials,
                    chat_request=request
                )
                return JSONResponse(content=response_data)
            except HTTPException as http_exc:
                raise http_exc
            except Exception as e:
                logger.error(f"Error during 'redeploy' (cloud-local) for Request ID {request_id}, Instance ID {request.instance_id}: {str(e)}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error during 'redeploy' (cloud-local) action: {str(e)}")
        elif request.deployment_mode == "cloud-hosted":
            if not request.instance_id: # This is the cluster_name
                raise HTTPException(status_code=400, detail="Instance ID (EKS cluster name) is required for cloud-hosted redeploy.")
            if not request.github_repo_url:
                raise HTTPException(status_code=400, detail="GitHub repository URL is required for redeploy.")
            if not request.target_namespace: # Assuming namespace is always needed
                raise HTTPException(status_code=400, detail="Target namespace is required for redeploy.")
            if not request.aws_credentials:
                raise HTTPException(status_code=400, detail="AWS credentials are required for cloud-hosted redeploy.")

            logger.info(f"Cloud-hosted redeploy requested for EKS cluster: {request.instance_id} (Instance Name: {request.instance_name}), repo: {request.github_repo_url}")
            response_data: Dict[str, Any] = {}
            try:
                response_data = await handle_cloud_hosted_redeploy(
                    cluster_name=request.instance_id,
                    repo_url=request.github_repo_url,
                    namespace=request.target_namespace,
                    aws_creds=request.aws_credentials,
                    chat_request=request
                )
                return JSONResponse(content=response_data)
            except HTTPException as http_exc:
                raise http_exc
            except Exception as e:
                logger.error(f"Error during 'redeploy' (cloud-hosted) for Request ID {request_id}, Cluster {request.instance_id}: {str(e)}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error during 'redeploy' (cloud-hosted) action: {str(e)}")
        else:
            # Fallback for other deployment modes under redeploy
            raise HTTPException(status_code=501, detail=f"Redeploy not implemented for {request.deployment_mode}")

    elif request.action == "scale":
        if request.deployment_mode == "cloud-local":
            if not request.instance_id:
                raise HTTPException(status_code=400, detail="Instance ID ('instance_id') is required for 'scale' action.")
            if not request.public_ip:
                raise HTTPException(status_code=400, detail="Public IP ('public_ip') is required for 'scale' action on cloud-local instance.")
            if not request.ec2_key_name:
                 raise HTTPException(status_code=400, detail="EC2 key name ('ec2_key_name') is required for 'scale' action on cloud-local instance.")
            if not request.target_namespace:
                raise HTTPException(status_code=400, detail="Target Kubernetes namespace ('target_namespace') is required for 'scale' action.")
            if request.scale_replicas is None or not isinstance(request.scale_replicas, int) or request.scale_replicas < 0:
                raise HTTPException(status_code=400, detail="A valid, non-negative integer for 'scale_replicas' is required for 'scale' action.")

            logger.info(f"Scale action requested for instance: {request.instance_id} (Instance Name: {request.instance_name}, IP: {request.public_ip}) to {request.scale_replicas} replicas in namespace {request.target_namespace}")
            response_data: Dict[str, Any] = {}
            try:
                # AWS creds might be optional for pure SSH/kubectl scale, but good to pass if available
                if not request.aws_credentials:
                    logger.warning("AWS credentials not provided for cloud-local scale, proceeding but some operations might fail if they require AWS API access.")

                response_data = await handle_cloud_local_scale(
                    instance_id=request.instance_id, public_ip=request.public_ip,
                    ec2_key_name=request.ec2_key_name, namespace=request.target_namespace,
                    replicas=request.scale_replicas, aws_creds=request.aws_credentials,
                    chat_request=request
                )
                return JSONResponse(content=response_data)
            except HTTPException as http_exc:
                raise http_exc
            except Exception as e:
                logger.error(f"Error during 'scale' action for Request ID {request_id}, Instance ID {request.instance_id}: {str(e)}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Error during 'scale' action: {str(e)}")
        else:
            raise HTTPException(status_code=501, detail=f"Scale action for mode '{request.deployment_mode}' is not yet implemented.")

    # Fallthrough to standard Azure OpenAI chat logic
    if not client:
        raise HTTPException(status_code=500, detail="Azure OpenAI client is not configured.")
    if not settings.AZURE_OPENAI_DEPLOYMENT:
        raise HTTPException(status_code=500, detail="Azure OpenAI deployment name is not configured.")

    formatted_messages: List[Dict[str, Any]] = []
    has_system_prompt = any(msg.role == "system" for msg in request.messages)
    if not has_system_prompt:
        formatted_messages.append({"role": "system", "content": MCP_SYSTEM_PROMPT})
    for msg in request.messages:
        formatted_messages.append(msg.model_dump(exclude_none=True))

    try:
        if request.stream:
            logger.info("Streaming request: Tool calls will be ignored in this mode for now.")
            async def stream_generator() -> AsyncGenerator[str, None]:
                try:
                    stream_params = {
                        "model": settings.AZURE_OPENAI_DEPLOYMENT, "messages": formatted_messages,
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
            first_pass_params: Dict[str, Any] = {
                "model": settings.AZURE_OPENAI_DEPLOYMENT, "messages": formatted_messages,
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
                    try: arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        tool_output_content = {"status": "error", "error_message": f"Invalid arguments JSON: {tool_call.function.arguments}"}
                    else: tool_output_content = await execute_tool(tool_name, arguments)
                    current_messages_for_llm.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": json.dumps(tool_output_content)})

                logger.debug(f"Second pass to LLM with history including tool results: {current_messages_for_llm}")
                completion = client.chat.completions.create(model=settings.AZURE_OPENAI_DEPLOYMENT, messages=current_messages_for_llm,
                    temperature=request.temperature, top_p=request.top_p, n=request.n, stream=False, stop=request.stop,
                    max_tokens=request.max_tokens, presence_penalty=request.presence_penalty, frequency_penalty=request.frequency_penalty, user=request.user)
                response_message = completion.choices[0].message

            response_choices: List[Choice] = [Choice(index=0, message=ChatMessage(role=response_message.role or "assistant", content=response_message.content), finish_reason=completion.choices[0].finish_reason)]
            response_usage = Usage(prompt_tokens=completion.usage.prompt_tokens if completion.usage else 0, completion_tokens=completion.usage.completion_tokens if completion.usage else 0, total_tokens=completion.usage.total_tokens if completion.usage else 0)
            return ChatCompletionResponse(id=completion.id or request_id, created=completion.created or created_timestamp, model=completion.model or settings.AZURE_OPENAI_DEPLOYMENT, choices=response_choices, usage=response_usage)

    except APIError as e:
        logger.error(f"Azure OpenAI API Error for Request ID {request_id}: Status {e.status_code} - {e.message}", exc_info=True)
        raise HTTPException(status_code=e.status_code or 500, detail=f"Azure OpenAI API Error: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error in chat completion for Request ID {request_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
