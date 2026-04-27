import asyncio
import os
import json
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncIterator

import boto3
from anthropic import AsyncAnthropic, DefaultAioHttpClient
from huggingface_hub import AsyncInferenceClient
from ollama import AsyncClient as OllamaAsyncClient
from openai import AsyncOpenAI, AsyncAzureOpenAI

from config import Config
from models import Message, ToolCall, ModelResponse
from observability import get_logger

logger = get_logger(__name__)

# Default headers for all API calls - can be overridden by providing custom headers when initializing backends.
HEADERS_FILE = os.path.join(os.path.dirname(__file__), "headers.json")
if not os.path.exists(HEADERS_FILE):
    logger.warning(
        "headers.json file not found. Using default headers. Please create a headers.json file with appropriate headers for better observability and cost tracking."
    )
    DEFAULT_HEADERS = {
        "User-Agent": "research-agent/1.0",
        "x-llm-agent": "research-agent",
        "x-llm-application-name": "research-agent",
    }
else:
    with open(HEADERS_FILE, "r") as f:
        DEFAULT_HEADERS = json.load(f)


# Default max tokens for generation - can be overridden in generate() calls.
# Reads MAX_TOKENS from the environment so it stays in sync with config.py.
# Falls back to 4096 (matches config.py default) if not set.
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))

# Default temperature for generation - can be overridden in generate() calls
TEMPERATURE = 0.7


class ModelBackend(ABC):
    @abstractmethod
    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        """Generate a response from the model"""
        pass

    @abstractmethod
    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        """Stream a response from the model"""
        pass


class OpenAIBackend(ModelBackend):
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        default_headers: Optional[Dict[str, str]] = None,
    ):
        self.model = (
            model  # Default model name. Can be overridden in generate() call if needed.
        )
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers or DEFAULT_HEADERS,
        )

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        try:
            messages = [{"role": m.role, "content": m.content} for m in messages]
            logger.debug(
                "Generating response with OpenAIBackend: model=%s, tools=%s, messages=%s",
                model or self.model,
                json.dumps(messages, indent=2),
                tools if tools else [],
            )
            response = await self.client.responses.create(
                model=model if model else self.model,
                input=messages,
                max_output_tokens=max_tokens,
                tools=tools if tools else [],
                tool_choice="auto" if tools else "none",
                # store=True,  # Used to maintain state from turn to turn, preserving reasoning and tool context
                # temperature=temperature, # Doesn't work with every model?
            )

            # Capture tool calls if present
            tool_calls = []
            for item in response.output:
                if item.type == "function_call":
                    tool_calls.append(
                        ToolCall(
                            name=item.name,
                            description=item.type,
                            parameters=json.loads(item.arguments or "{}"),
                        )
                    )
            return ModelResponse(
                content=response.output_text,
                tool_calls=tool_calls,
                finish_reason="stop",
            )
        except Exception as e:
            logger.exception("Error in OpenAIBackend.generate: %s", str(e))
            return ModelResponse(content="", tool_calls=None, finish_reason="error")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        try:
            response = await self.client.responses.create(
                model=self.model,
                input=[{"role": m.role, "content": m.content} for m in messages],
                tools=tools if tools else [],
                tool_choice="auto" if tools else "none",
                stream=True,
            )
            async for event in response:
                if event.type == "response.output_text.delta":
                    yield event.delta

        except Exception as e:
            logger.exception("Error in OpenAIBackend.stream_generate: %s", str(e))
            yield ""


class AzureOpenAIBackend(ModelBackend):
    """Azure OpenAI backend implementation"""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment_name: str,
        api_version: str = "2024-02-15-preview",
        default_headers: Optional[Dict[str, str]] = None,
    ):
        # Default model. Can be overridden in generate() call if needed.
        # Azure OpenAI typically uses deployment names for models.
        self.model = deployment_name
        self.client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            azure_deployment=deployment_name,
            api_version=api_version,
            default_headers=default_headers or DEFAULT_HEADERS,
        )

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        try:
            logger.debug(
                "Generating response with AzureOpenAIBackend: deployment=%s, messages=%s, tools=%s",
                model or self.model,
                messages,
                ", ".join([t["name"] for t in tools]) if tools else "None",
            )
            response = await self.client.chat.completions.create(
                model=model if model else self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens,
                tools=tools if tools else [],
                tool_choice="auto" if tools else "none",
                # temperature=temperature, # Azure OpenAI may not support temperature for all models - test and enable if supported
            )
            message = response.choices[0].message
            if not message:
                return ModelResponse(content="", tool_calls=None)

            return ModelResponse(
                content=message.content or "",
                tool_calls=message.tool_calls,
            )
        except Exception as e:
            logger.exception("Error in AzureOpenAIBackend.generate: %s", str(e))
            raise RuntimeError(f"Azure OpenAI API error: {str(e)}")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=tools if tools else [],
                tool_choice="auto" if tools else "none",
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.exception("Error in AzureOpenAIBackend.stream_generate: %s", str(e))
            raise RuntimeError(f"Azure OpenAI API streaming error: {str(e)}")


class AWSOpenAIBackend(ModelBackend):
    """AWS OpenAI Compatible backend"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        default_headers: Optional[Dict[str, str]] = None,
    ):
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers=default_headers or DEFAULT_HEADERS,
        )
        self.model = model

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        try:
            logger.debug(
                "Generating response with AWSOpenAIBackend: model=%s, messages=%s, tools=%s",
                model or self.model,
                messages,
                tools if tools else [],
            )

            # AWS OpenAI expects tools in a specific format, so we need to convert our internal ToolCall objects to
            # the format expected by AWS OpenAI
            tool_calls = format_tool_calls(tools or [])

            # Call the AWS OpenAI API using the OpenAI-compatible client
            response = await self.client.chat.completions.create(
                model=model if model else self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens,
                tools=tool_calls,
                tool_choice="auto" if tools else "none",
                # temperature=temperature, # AWS OpenAI may not support temperature for all models
            )
            content = response.choices[0].message.content
            if not content:
                return ModelResponse(content="", tool_calls=None, finish_reason="error")

            logger.debug(
                "Received raw tool calls: %s", response.choices[0].message.tool_calls
            )

            # Capture tool calls if present.
            tool_calls = response.choices[0].message.tool_calls
            tool_calls = (
                [
                    ToolCall(
                        name=tc.function.name,
                        parameters=json.loads(tc.function.arguments or "{}"),
                        description=tc.type,
                    )
                    for tc in tool_calls
                ]
                if tool_calls
                else []
            )

            return ModelResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=response.choices[0].finish_reason,
            )
        except Exception as e:
            logger.exception("Error in AWSOpenAIBackend.generate: %s", str(e))
            return ModelResponse(content="", tool_calls=None, finish_reason="error")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        try:
            tool_calls = format_tool_calls(tools or [])
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=tool_calls,
                tool_choice="auto" if tools else "none",
                stream=True,
            )
            async for event in response:
                if event.choices and event.choices[0].delta.content:
                    yield event.choices[0].delta.content
        except Exception as e:
            logger.exception("Error in AWSOpenAIBackend.stream_generate: %s", str(e))
            yield ""
            return


class BedrockBackend(ModelBackend):
    """AWS Bedrock backend using native boto3 bedrock-runtime (converse API).

    Credentials are resolved via the standard boto3 chain:
    environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
    AWS_SESSION_TOKEN), ~/.aws/credentials, or an attached IAM role.
    """

    def __init__(
        self,
        model: str,
        region: str = "us-east-1",
        default_headers: Optional[Dict[str, str]] = None,
    ):
        self.model = model
        self.bedrock_client = boto3.client("bedrock-runtime", region_name=region)

    def _convert_messages(self, messages: List[Message]) -> tuple[list, list]:
        """Split system messages into a separate list; format the rest for Converse API."""
        system = []
        converse_messages = []
        for m in messages:
            if m.role == "system":
                system.append({"text": m.content})
            else:
                converse_messages.append(
                    {"role": m.role, "content": [{"text": m.content}]}
                )
        return system, converse_messages

    def _convert_tools(self, tools: Optional[List[Dict]]) -> Optional[Dict]:
        """Convert internal tool dicts to Bedrock Converse toolConfig format."""
        if not tools:
            return None
        tool_specs = []
        for t in tools:
            func = t.get("function", t)
            tool_specs.append(
                {
                    "toolSpec": {
                        "name": func.get("name", ""),
                        "description": func.get("description", ""),
                        "inputSchema": {"json": func.get("parameters", {})},
                    }
                }
            )
        return {"tools": tool_specs}

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        try:
            system, converse_messages = self._convert_messages(messages)
            tool_config = self._convert_tools(tools)

            logger.debug(
                "Generating response with BedrockBackend: model=%s, tools=%s",
                model or self.model,
                [t.get("function", t).get("name") for t in tools] if tools else [],
            )

            kwargs: Dict[str, Any] = {
                "modelId": model or self.model,
                "messages": converse_messages,
                "inferenceConfig": {
                    "maxTokens": max_tokens,
                    "temperature": temperature,
                },
            }
            if system:
                kwargs["system"] = system
            if tool_config:
                kwargs["toolConfig"] = tool_config

            response = await asyncio.to_thread(self.bedrock_client.converse, **kwargs)

            content_blocks = response["output"]["message"]["content"]
            text = ""
            tool_calls = []
            for block in content_blocks:
                if "text" in block:
                    text += block["text"]
                elif "toolUse" in block:
                    tc = block["toolUse"]
                    tool_calls.append(
                        ToolCall(
                            name=tc["name"],
                            description="",
                            parameters=tc.get("input", {}),
                        )
                    )

            stop_reason = response.get("stopReason", "end_turn")
            finish_reason = "stop" if stop_reason == "end_turn" else stop_reason

            return ModelResponse(
                content=text,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
            )
        except Exception as e:
            logger.exception("Error in BedrockBackend.generate: %s", str(e))
            return ModelResponse(content="", tool_calls=None, finish_reason="error")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        try:
            system, converse_messages = self._convert_messages(messages)
            tool_config = self._convert_tools(tools)

            kwargs: Dict[str, Any] = {
                "modelId": self.model,
                "messages": converse_messages,
            }
            if system:
                kwargs["system"] = system
            if tool_config:
                kwargs["toolConfig"] = tool_config

            def _collect_chunks() -> list[str]:
                chunks: list[str] = []
                resp = self.bedrock_client.converse_stream(**kwargs)
                for event in resp["stream"]:
                    if "contentBlockDelta" in event:
                        delta = event["contentBlockDelta"].get("delta", {})
                        if "text" in delta:
                            chunks.append(delta["text"])
                return chunks

            chunks = await asyncio.to_thread(_collect_chunks)
            for chunk in chunks:
                yield chunk
        except Exception as e:
            logger.exception("Error in BedrockBackend.stream_generate: %s", str(e))
            yield ""
            return


class GCPVertexAIBackend(ModelBackend):
    """Google Cloud Vertex AI backend - placeholder for future implementation"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        default_headers: Optional[Dict[str, str]] = None,
    ):
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            default_headers=default_headers or DEFAULT_HEADERS,
        )
        self.model = model

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        try:
            logger.debug(
                "Generating response with GCPVertexAIBackend: model=%s, messages=%s, tools=%s",
                model or self.model,
                messages,
                tools if tools else [],
            )

            # GCP Execution expects tools in a specific format, so we need to convert our internal
            # ToolCall objects to the format expected by GCP Vertex AI
            tool_calls = format_tool_calls(tools or [])

            # Call the GCP Vertex AI API using the OpenAI-compatible client
            response = await self.client.chat.completions.create(
                model=model if model else self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens,
                tools=tool_calls,
                tool_choice="auto" if tools else "none",
                # temperature=temperature, # Vertex AI may not support temperature for all models - test and enable if supported
            )
            content = response.choices[0].message.content or ""
            if not content:
                return ModelResponse(content="", tool_calls=[], finish_reason="error")

            raw_tool_calls = response.choices[0].message.tool_calls or []

            # Convert raw SDK ChatCompletionMessageFunctionToolCall objects to
            # internal ToolCall dataclass objects (same pattern as BedrockBackend).
            converted_tool_calls = [
                ToolCall(
                    name=tc.function.name,
                    parameters=json.loads(tc.function.arguments or "{}"),
                    description=tc.type,
                )
                for tc in raw_tool_calls
            ]

            return ModelResponse(
                content=content,
                tool_calls=converted_tool_calls,
                finish_reason=response.choices[0].finish_reason,
            )
        except Exception as e:
            logger.exception("Error in GCPVertexAIBackend.generate: %s", str(e))
            return ModelResponse(content="", tool_calls=None, finish_reason="error")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        try:
            tool_calls = format_tool_calls(tools or [])
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=tool_calls,
                tool_choice="auto" if tools else "none",
                stream=True,
            )
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.exception("Error in GCPVertexAIBackend.stream_generate: %s", str(e))
            yield ""
            return


class AnthropicBackend(ModelBackend):
    """Anthropic backend using the official SDK with aiohttp for improved async performance."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ):
        self.model = model
        client_kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "http_client": DefaultAioHttpClient(),
            "default_headers": default_headers or DEFAULT_HEADERS,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = AsyncAnthropic(**client_kwargs)

    def _split_messages(self, messages: List[Message]) -> tuple[str, list]:
        """Extract system prompt and format remaining messages for Anthropic."""
        system_parts = []
        formatted = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
            else:
                formatted.append({"role": m.role, "content": m.content})
        return "\n".join(system_parts), formatted

    def _convert_tools(self, tools: Optional[List[Dict]]) -> list:
        """Convert internal tool dicts to Anthropic tool format."""
        if not tools:
            return []
        result = []
        for t in tools:
            func = t.get("function", t)
            result.append(
                {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "input_schema": func.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                }
            )
        return result

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        try:
            system, formatted_messages = self._split_messages(messages)
            anthropic_tools = self._convert_tools(tools)

            logger.debug(
                "Generating response with AnthropicBackend: model=%s, tools=%s",
                model or self.model,
                [t["name"] for t in anthropic_tools] if anthropic_tools else [],
            )

            kwargs: Dict[str, Any] = {
                "model": model or self.model,
                "messages": formatted_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if system:
                kwargs["system"] = system
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools
                kwargs["tool_choice"] = {"type": "auto"}

            response = await self.client.messages.create(**kwargs)

            text = ""
            tool_calls = []
            for block in response.content:
                if block.type == "text":
                    text += block.text
                elif block.type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            name=block.name,
                            description="tool_use",
                            parameters=(
                                block.input if isinstance(block.input, dict) else {}
                            ),
                        )
                    )

            finish_reason = (
                "stop"
                if response.stop_reason == "end_turn"
                else (response.stop_reason or "stop")
            )
            return ModelResponse(
                content=text,
                tool_calls=tool_calls if tool_calls else None,
                finish_reason=finish_reason,
            )
        except Exception as e:
            logger.exception("Error in AnthropicBackend.generate: %s", str(e))
            return ModelResponse(content="", tool_calls=None, finish_reason="error")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        try:
            system, formatted_messages = self._split_messages(messages)
            anthropic_tools = self._convert_tools(tools)

            kwargs: Dict[str, Any] = {
                "model": self.model,
                "messages": formatted_messages,
                "max_tokens": MAX_TOKENS,
            }
            if system:
                kwargs["system"] = system
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools
                kwargs["tool_choice"] = {"type": "auto"}

            async with self.client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            logger.exception("Error in AnthropicBackend.stream_generate: %s", str(e))
            yield ""
            return


class OllamaBackend(ModelBackend):
    def __init__(
        self,
        model: str,
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.client = OllamaAsyncClient(host=base_url)

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        logger.debug(
            "Generating response with OllamaBackend: model=%s, messages=%s, tools=%s",
            model or self.model,
            messages,
            tools if tools else [],
        )

        try:
            formatted_tools = _format_tools_for_ollama(tools or [])
            response = await self.client.chat(
                model=model or self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=formatted_tools,
                options={"num_predict": max_tokens, "temperature": temperature},
            )

            tool_calls = []
            if response.message.tool_calls:
                tool_calls = [
                    ToolCall(
                        name=tc.function.name,
                        description="",
                        parameters=dict(tc.function.arguments),
                    )
                    for tc in response.message.tool_calls
                ]

            return ModelResponse(
                content=response.message.content or "",
                tool_calls=tool_calls,
                finish_reason="stop",
            )
        except Exception as e:
            logger.exception("Error in OllamaBackend.generate: %s", str(e))
            return ModelResponse(content="", tool_calls=[], finish_reason="error")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        try:
            formatted_tools = _format_tools_for_ollama(tools or [])
            stream = await self.client.chat(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=formatted_tools,
                options={"num_predict": MAX_TOKENS, "temperature": TEMPERATURE},
                stream=True,
            )
            async for chunk in stream:
                if chunk.message.content:
                    yield chunk.message.content
        except Exception as e:
            logger.exception("Error in OllamaBackend.stream_generate: %s", str(e))
            yield ""
            return


class HuggingFaceBackend(ModelBackend):
    """HuggingFace Inference backend using the official huggingface_hub SDK."""

    def __init__(self, base_url: str, model: str = None, api_key: Optional[str] = None):
        self.model = model
        self.client = AsyncInferenceClient(
            model=model,
            token=api_key,
            base_url=base_url,
            headers=DEFAULT_HEADERS,
        )

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> ModelResponse:
        active_model = model or self.model
        logger.debug(
            "Generating response with HuggingFaceBackend: model=%s, messages=%s, tools=%s",
            active_model,
            messages,
            tools if tools else [],
        )
        try:
            response = await self.client.chat.completions.create(
                model=active_model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=tools if tools else [],
                tool_choice="auto" if tools else "none",
                max_tokens=max_tokens,
                temperature=temperature,
            )
            message = response.choices[0].message
            tool_calls = []
            if message.tool_calls:
                tool_calls = [
                    ToolCall(
                        name=tc.function.name,
                        description="",
                        parameters=json.loads(tc.function.arguments or "{}"),
                    )
                    for tc in message.tool_calls
                ]
            return ModelResponse(
                content=message.content or "",
                tool_calls=tool_calls or None,
                finish_reason=response.choices[0].finish_reason or "stop",
            )
        except Exception as e:
            logger.exception("Error in HuggingFaceBackend.generate: %s", str(e))
            return ModelResponse(content="", tool_calls=None, finish_reason="error")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.exception("Error in HuggingFaceBackend.stream_generate: %s", str(e))
            yield ""
            return


def _strip_none_values(obj: Any) -> Any:
    """Recursively remove None values from JSON schemas"""
    if isinstance(obj, dict):
        return {k: _strip_none_values(v) for k, v in obj.items() if v is not None}
    elif isinstance(obj, list):
        return [_strip_none_values(item) for item in obj if item is not None]
    return obj


def _format_tools_for_ollama(tools: list[dict]) -> List[Dict[str, Any]]:
    """Convert flat tool format to Ollama's nested function format and strip None values"""
    formatted = []
    for t in tools:
        func = t.get("function", t)
        params = func.get("parameters", {})
        formatted.append(
            {
                "type": "function",
                "function": {
                    "name": func.get("name", ""),
                    "description": func.get("description", ""),
                    "parameters": _strip_none_values(params) if params else {},
                },
            }
        )
    return formatted


def format_tool_calls(tool_calls: list[dict]) -> List[Dict[str, Any]]:
    """Convert internal ToolCall objects to the format expected by AWS and GPC backends"""
    formatted = []
    for tc in tool_calls:
        formatted.append(
            {
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "description": tc["description"],
                    "parameters": tc["parameters"],
                },
            }
        )
    return formatted


def create_model_backend(config: Config) -> ModelBackend:
    """Factory function to create the appropriate model backend"""
    backend_type = config.model_backend.lower()

    if backend_type == "openai":
        return OpenAIBackend(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            model=config.openai_model,
        )

    elif backend_type == "aws":
        return AWSOpenAIBackend(
            base_url=config.aws_base_url,
            api_key=config.aws_api_key,
            model=config.aws_model,
        )

    elif backend_type == "bedrock":
        return BedrockBackend(
            model=config.aws_model,
            region=config.aws_region,
        )

    elif backend_type == "azure":
        return AzureOpenAIBackend(
            endpoint=config.azure_endpoint,
            api_key=config.azure_api_key,
            deployment_name=config.azure_deployment_name,
            api_version=config.azure_api_version,
        )

    elif backend_type == "gcp":
        return GCPVertexAIBackend(
            base_url=config.gcp_base_url,
            api_key=config.gcp_api_key,
            model=config.gcp_model,
        )

    elif backend_type == "ollama":
        return OllamaBackend(base_url=config.ollama_base_url, model=config.ollama_model)

    elif backend_type == "anthropic":
        return AnthropicBackend(
            api_key=config.anthropic_api_key,
            model=config.anthropic_model,
            base_url=config.anthropic_base_url or None,
        )

    elif backend_type == "huggingface":
        return HuggingFaceBackend(
            base_url=config.huggingface_base_url,
            model=config.huggingface_model,
            api_key=config.huggingface_api_key,
        )

    else:
        raise ValueError(f"Unknown model backend: {backend_type}")
