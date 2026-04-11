import os
import aiohttp
import json
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncIterator

from ollama import AsyncClient as OllamaAsyncClient, ChatResponse
from openai import AsyncOpenAI, AsyncAzureOpenAI

from config import Config
from models import Message, ToolCall, ModelResponse
from observability import get_logger

logger = get_logger(__name__)

# This header is used by the AI Gateway to track application specific calls and token usage.
# Helpful for observability and cost tracking when using OpenAI, Azure OpenAI, and AWS Bedrock APIs.
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


class BedrockBackend(ModelBackend):
    """AWS Bedrock backend"""

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
                "Generating response with BedrockBackend: model=%s, messages=%s, tools=%s",
                model or self.model,
                messages,
                tools if tools else [],
            )

            # Bedrock expects tools in a specific format, so we need to convert our internal ToolCall objects to
            # the format expected by Bedrock
            tool_calls = format_tool_calls(tools or [])

            # Call the Bedrock API using the OpenAI-compatible client
            response = await self.client.chat.completions.create(
                model=model if model else self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                max_tokens=max_tokens,
                tools=tool_calls,
                tool_choice="auto" if tools else "none",
                # temperature=temperature, # Bedrock may not support temperature for all models
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
            logger.exception("Error in BedrockBackend.generate: %s", str(e))
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
            response: ChatResponse = await self.client.chat(
                model=model or self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=tools or [],
                options={"num_predict": max_tokens, "temperature": temperature},
            )

            tool_calls = []
            if response.message.tool_calls:
                tool_calls = [
                    ToolCall(
                        name=tc.function.name,
                        description="",
                        parameters=dict(tc.function.arguments) or {},
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
            stream = await self.client.chat(
                model=self.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                tools=tools or [],
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
    """For self-hosted HuggingFace models with text-generation-inference or similar"""

    def __init__(self, base_url: str, model: str = None):
        self.base_url = base_url
        self.model = model

    async def generate(
        self,
        model: Optional[str],
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> ModelResponse:
        # Convert messages to prompt format
        prompt = self._messages_to_prompt(messages)

        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": max_tokens,
                "return_full_text": False,
                "temperature": temperature,
            },
        }

        logger.debug(
            "Generating response with HuggingFaceBackend: model=%s, messages=%s, tools=%s",
            model or self.model,
            messages,
            tools if tools else [],
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/generate", json=payload
                ) as response:
                    result = await response.json()
                    response.raise_for_status()
                    content = (
                        result[0]["generated_text"]
                        if isinstance(result, list)
                        else result["generated_text"]
                    )

                    # Parse tool calls from structured output if present
                    tool_calls = self._parse_tool_calls(content) if tools else None

                    return ModelResponse(
                        content=content, tool_calls=tool_calls, finish_reason="stop"
                    )
        except Exception as e:
            logger.exception("Error in HuggingFaceBackend.generate: %s", str(e))
            return ModelResponse(content="", tool_calls=None, finish_reason="error")

    async def stream_generate(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
    ) -> AsyncIterator[str]:
        prompt = self._messages_to_prompt(messages)

        payload = {
            "inputs": prompt,
            "parameters": {"max_new_tokens": 2048},
            "stream": True,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/generate_stream", json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.content:
                        if line:
                            data = json.loads(line)
                            if "token" in data and "text" in data["token"]:
                                yield data["token"]["text"]
        except Exception as e:
            logger.exception("Error in HuggingFaceBackend.stream_generate: %s", str(e))
            yield ""
            return

    @staticmethod
    def _messages_to_prompt(messages: List[Message]) -> str:
        """Convert messages to a prompt string - customize based on your model"""
        prompt = ""
        for msg in messages:
            if msg.role == "system":
                prompt += f"System: {msg.content}\n\n"
            elif msg.role == "user":
                prompt += f"User: {msg.content}\n\n"
            elif msg.role == "assistant":
                prompt += f"Assistant: {msg.content}\n\n"
        prompt += "Assistant: "
        return prompt

    def _parse_tool_calls(self, content: str) -> Optional[List[ToolCall]]:
        """Parse tool calls from model output - implement based on your prompting strategy"""
        # This is a simple example - you'd need to implement proper parsing
        # based on how you prompt the model to use tools
        try:
            if "<tool_call>" in content:
                # Parse structured tool calls from content
                # This is placeholder logic
                return None
        except Exception as e:
            logger.exception(
                "Error parsing tool calls from HuggingFace output: %s", str(e)
            )
            raise
        return None


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
        return BedrockBackend(
            base_url=config.aws_base_url,
            api_key=config.aws_api_key,
            model=config.aws_model,
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

    elif backend_type == "huggingface":
        return HuggingFaceBackend(
            base_url=config.huggingface_base_url, model=config.huggingface_model
        )

    else:
        raise ValueError(f"Unknown model backend: {backend_type}")
