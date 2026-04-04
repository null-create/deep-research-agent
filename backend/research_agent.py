import os
import json
import uuid
import time
import asyncio
import argparse
from enum import Enum
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, AsyncIterator

from mcp_client import MCPServerRegistry, MCPClient, create_mcp_registry
from model_backend import (
    create_model_backend,
    Message,
    ModelResponse,
    ModelBackend,
    OpenAIBackend,
    OllamaBackend,
    AzureOpenAIBackend,
    BedrockBackend,
    GCPVertexAIBackend,
    HuggingFaceBackend,
)
from config import Config
from context import ResearchContext
from models import (
    ResearchStep,
    ResearchPlan,
    StepStatus,
    SynthesisResult,
    ResponseMessage,
)
from observability import get_logger

logger = get_logger(__name__)


class ResearchAgent:
    """Main research agent orchestrator"""

    def __init__(self, model_backend: ModelBackend, mcp_registry: MCPServerRegistry):
        self.model = model_backend
        self.mcp_servers = mcp_registry
        self.conversation_history: List[Message] = []
        self.query: str = ""
        self.step_results: List[str] = []
        self.memory_context: str = ""
        self._pending_plan: Optional[ResearchPlan] = None
        self._pending_query: Optional[str] = None
        self._pending_memory_context: Optional[str] = None
        self._step_context: ResearchContext = ResearchContext()

    def _clear_state(self) -> None:
        """Clear the agent's state after completing a research session or denying a plan"""
        self.query = ""
        self.step_results = []
        self.memory_context = ""
        self.conversation_history = []
        self._pending_plan = None
        self._pending_query = None
        self._pending_memory_context = None
        self._step_context = ResearchContext()

    async def generate_plan(self, query: str) -> AsyncIterator[ResponseMessage]:
        """
        Phase 1 only: check memory and generate a plan.
        Stops after yielding the plan — execution happens in execute_approved_plan().
        """
        yield ResponseMessage(
            type="status",
            message="Received research query. Starting research process...",
        )

        # Step 1: Check long-term memory
        yield ResponseMessage(
            type="status",
            message="Checking long-term memory for relevant information...",
        )
        memory_context = await self._check_memory(query)

        # Step 1.5: Check current files for relevant information (optional enhancement, not implemented in this version)
        # yield {
        #     "type": "status",
        #     "message": "Checking current files for relevant information...",
        # }
        # file_data = "No relevant files found."  # Placeholder for file checking logic

        # Step 2: Generate research plan
        yield ResponseMessage(type="status", message="Generating research plan...")
        plan: ResearchPlan = await self._generate_plan(query, memory_context)

        # Store for later execution on approval or modification
        self._pending_plan = plan
        self._pending_query = query
        self._pending_memory_context = memory_context

        yield ResponseMessage(
            type="plan",
            message="Research plan generated. Awaiting user approval.",
            plan=plan.to_dict(),
        )

    async def execute_approved_plan(self) -> AsyncIterator[ResponseMessage]:
        """
        Phase 2: execute the previously generated and approved plan.
        Called by the WebSocket handler after the user approves.
        """
        if not self._pending_plan:
            yield ResponseMessage(
                type="error",
                message="No pending plan to execute. Please generate a plan first.",
            )
            return

        plan = self._pending_plan
        query = self._pending_query

        # Clear pending state
        self._pending_query = None
        self._pending_memory_context = None

        # Step 3: Execute each step
        # NOTE: this will increase the context window usage significantly! We may want to implement a more efficient
        # way to pass context to later steps without including all previous results in the prompt, such as by using
        # embeddings and vector search over previous step results instead of raw text.
        failures = 0
        all_step_results = []
        failure_messages = []
        for step in plan.steps:
            yield ResponseMessage(
                type="step_start",
                message=f"Starting step {step.id}: {step.description}",
                data={"step": step.to_dict()},
            )
            try:
                result = await self._execute_step(step, query)
                step.status = StepStatus.COMPLETED
                step.result = result
                all_step_results.append(result)
                self._step_context.add_result(step.id, step.description, result)

                logger.info("Completed step %d: %s", step.id, step.description)
                yield ResponseMessage(
                    type="step_complete",
                    message=f"Completed step {step.id}",
                    data={"step": step.to_dict(), "result": result},
                )
                logger.info("Step %d sent", step.id)
            except Exception as e:
                step.status = StepStatus.FAILED
                step.error = str(e)
                failures += 1
                failure_messages.append(f"Step {step.id} failed: {str(e)}")
                logger.error("Error executing step %d: %s", step.id, str(e))
                yield ResponseMessage(
                    type="step_failed",
                    message=f"Step {step.id} failed",
                    error=str(e),
                    data={"step": step.to_dict()},
                )

        # Don't proceed to synthesis if all steps failed
        if failures == len(plan.steps):
            logger.error(
                "All steps in the research plan failed. Cannot proceed to synthesis."
            )
            failure_message = [
                Message(
                    role="system",
                    content="""You are a research planning assistant. The research execution failed for all steps in 
                    the plan. Please analyze the reasons for failure based on the following information and provide a 
                    message to inform the user about the failure and suggest next steps.""",
                ),
                Message(
                    role="user",
                    content=f"""Research Query: {query}
                    
                    Research Plan: {json.dumps(plan.to_dict())}
                    
                    Failure Details:
                    {' ,'.join(failure_messages) if failure_messages else 'No specific failure messages available.'}""",
                ),
            ]
            model = self._select_model_for_step("failure message")
            failure_response = await self.model.generate(model, failure_message)
            yield ResponseMessage(type="error", message=failure_response.content)
            self._clear_state()
            return

        # Save initial query and step results for synthesis step
        self.step_results = all_step_results
        self.query = query

        message = (
            f"Completed execution of research plan with {failures} failure(s) with plan containing {len(plan.steps)} steps. "
            f"Synthesis may be affected. Failure details: {' ,'.join(failure_messages)}"
            if failures > 0
            else "Completed execution of research plan with no failures."
        )
        yield ResponseMessage(
            type="research_complete", message=f"{message} Proceeding to synthesis..."
        )

    async def synthesize_results(self) -> AsyncIterator[ResponseMessage]:
        """Synthesize all findings into insights and recommendations (synchronous version for testing)"""
        if not self.query or not self.step_results:
            yield ResponseMessage(
                type="error",
                message="No research results available to synthesize. Please execute a research plan first.",
            )
            return

        # Step 4: Synthesize findings
        yield ResponseMessage(type="status", message="Synthesizing findings...")
        synthesis = await self._synthesize_findings(self.query, self.step_results)
        yield ResponseMessage(
            type="synthesis", message="Synthesis complete.", data=synthesis.model_dump()
        )

        # Step 5: Save to memory
        yield ResponseMessage(
            type="status", message="Saving results to long-term memory..."
        )
        await self._save_to_memory(
            self.query, self._pending_plan, self.step_results, synthesis
        )
        # yield {"type": "research_complete", "message": "Research complete!"}
        yield ResponseMessage(type="research_complete", message="Research complete!")

        # Step 6: Generate final report
        yield ResponseMessage(
            type="status", message="Generating final research report..."
        )
        final_document = await self._generate_final_document(
            self.query, synthesis.model_dump()
        )
        yield ResponseMessage(
            type="status",
            message="Report was generated successfully. Saving as artifact...",
        )

        # Step 7: Save with file_handler tool call
        file_client = self.mcp_servers.get("file_handler")
        if file_client:
            document_filename = f"research_report_{uuid.uuid4()}.txt"
            tool_args = {
                "file_name": document_filename,
                "content": final_document,
                "encoding": "utf-8",
                "mode": "write",
                "max_file_size": 100 * 1024 * 1024,  # 100 MB limit for the file
                "chunk_size": 5 * 1024 * 1024,  # 5 MB chunk size for uploading
            }
            save_result = await file_client.call_tool("write_file", tool_args)
            if "error" in save_result:
                yield ResponseMessage(
                    type="error",
                    message=f"Failed to save final document: {save_result['error']}",
                    error=save_result["error"],
                )
                return
            file_url = save_result.get("url", "")
            if not file_url:
                yield ResponseMessage(
                    type="error",
                    message=f"Failed to save final document: No URL returned from file handler.",
                )
                return
            yield ResponseMessage(
                type="status",
                message=f"Final document saved successfully!",
                data={"url": file_url},
            )
        else:
            yield ResponseMessage(
                type="error",
                message=f"File handler MCP server not available. Failed to save final document.",
            )
            return

        # Clear states after completion
        self._clear_state()

    async def modify_plan(self, plan_id: str, feedback: str) -> ResponseMessage:
        """Modify a pending plan based on user feedback (for simplicity, we only handle one pending plan at a
        time in this implementation)"""
        if not self._pending_plan:
            return ResponseMessage(
                type="error",
                message="No pending plan to modify. Please generate a plan first.",
            )

        if self._pending_plan.id == plan_id:
            logger.info(
                "Modifying plan with ID %s based on user feedback: %s",
                plan_id,
                feedback,
            )
            try:
                # Generate a new plan based on the original query, memory context, and user feedback
                original_plan = self._pending_plan
                modified_plan = await self._generate_plan(
                    self.query, self.memory_context, feedback
                )

                # Create updated message from Agent
                model = self._select_model_for_step("update message")
                plan_response = await self.model.generate(
                    model=model,
                    messages=[
                        Message(
                            role="system",
                            content=f"""You are a research planning assistant currently in the process of helping a user 
                            with their research. 
                            
                            The user provided feedback on the original research plan that you generated, 
                            and you have created an updated research plan based on that feedback. Please analyze the 
                            differences between the original plan and the updated plan, and provide a message to inform 
                            the user about the changes you made to the plan based on their feedback, and any important 
                            considerations they should be aware of with the updated plan. Be sure to highlight any 
                            significant changes to the research steps, the reasoning behind those changes, and how the 
                            updated plan better addresses the user's research query based on their feedback.
                            
                            The original research plan with the following details was modified by the user based 
                            on their feedback. Please provide a message to inform the user about the updated plan and 
                            next steps.                    
                            """,
                        ),
                        Message(
                            role="user",
                            content=f"""
                            Original plan: {json.dumps(original_plan.to_dict(), indent=2)}
                            
                            Updated plan: {json.dumps(modified_plan.to_dict(), indent=2)}""",
                        ),
                    ],
                )

                # Update the pending plan with the modified version
                self._pending_plan = modified_plan
                return ResponseMessage(
                    type="plan",
                    message=plan_response.content,
                    plan=modified_plan.to_dict(),
                )
            except Exception as e:
                logger.exception("Error modifying plan: %s", str(e))
                return ResponseMessage(
                    type="error",
                    message=f"Failed to modify the plan based on feedback. Error: {str(e)}",
                    error=str(e),
                )
        else:
            return ResponseMessage(
                type="error",
                message="No pending plan found with the given ID. Cannot modify.",
            )

    async def deny_plan(self, plan_id: int) -> ResponseMessage:
        """Deny a pending plan (for simplicity, we only handle one pending plan at a time in this implementation)"""
        if self._pending_plan and self._pending_plan.id == plan_id:
            logger.info(
                "Plan with ID %d denied by user. Clearing pending plan and state.",
                plan_id,
            )

            original_query = self.query
            previous_plan = self._pending_plan
            self._clear_state()

            # Generate a message to inform the user about the denial and suggest next steps
            messages = [
                Message(
                    role="system",
                    content="""You are a research planning assistant.""",
                ),
                Message(
                    role="user",
                    content=f"""The research plan with the following details was denied by the user. Please provide a 
                    message to inform the user about the denial and suggest next steps
    
                    Original query: {original_query}
    
                    Previous plan: {json.dumps(previous_plan.to_dict(), indent=2)}""",
                ),
            ]
            model = self._select_model_for_step("plan denial message")
            response = await self.model.generate(model, messages)
            return ResponseMessage(type="plan_denied", message=response.content)
        else:
            return ResponseMessage(
                type="error",
                message="No pending plan found with the given ID. Cannot deny.",
            )

    def get_plan_steps(self, plan_id) -> List[ResearchStep]:
        """Get the steps for a given plan (placeholder for potential future implementation with multiple plans)"""
        if self._pending_plan and self._pending_plan.id == plan_id:
            return self._pending_plan.steps
        return []

    async def chat(self, message: str) -> AsyncIterator[str]:
        """Simple chat interface for testing the model backend independently of the research process

        Does not invoke any tools or use any context from previous steps, just a simple back-and-forth with the
        model to test streaming responses.
        """
        system_prompt = """You are a helpful research assistant. Answer the user's message based on your 
            knowledge and capabilities."""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=message),
        ]
        async for response_chunk in self.model.stream_generate(messages, []):
            yield response_chunk

    def _select_model_for_step(self, step: ResearchStep | str) -> str:
        """Select the appropriate model for a given research step based on its description and complexity"""
        if isinstance(step, str):
            description = step.lower()
        else:
            description = step.description.lower()

        # Use the most powerful model available for synthesis and insight generation
        if any(
            keyword in description
            for keyword in [
                "plan",
                "synthesize",
                "insight",
                "pattern",
                "analyze",
                "summarize",
                "research",
                "develop",
                "assess",
                "assessment",
                "recommendation",
                "creative application",
                "document",
            ]
        ):
            if isinstance(self.model, OpenAIBackend):
                return "gpt-5.2"
            elif isinstance(self.model, AzureOpenAIBackend):
                return "gpt-5.2"
            elif isinstance(self.model, BedrockBackend):
                return "global.anthropic.claude-sonnet-4-6"
            elif isinstance(self.model, GCPVertexAIBackend):
                return "gemini-2.5-pro"
            elif isinstance(self.model, OllamaBackend):
                return "nemotron-3-nano"
            else:
                raise ValueError(
                    f"Unsupported model backend for synthesis steps: {type(self.model)}"
                )

        # Use a more efficient model for information gathering and tool execution steps
        elif any(
            keyword in description
            for keyword in [
                "search",
                "find",
                "gather",
                "execute",
                "retrieve",
                "scrape",
                "follow links",
            ]
        ):
            if isinstance(self.model, OpenAIBackend):
                return "gpt-5-nano"
            elif isinstance(self.model, AzureOpenAIBackend):
                return "gpt-5-nano"
            elif isinstance(self.model, BedrockBackend):
                return "global.anthropic.claude-haiku-4-5-20251001-v1:0"
            elif isinstance(self.model, GCPVertexAIBackend):
                return "gemini-2.5-nano"
            elif isinstance(self.model, OllamaBackend):
                return "llama3.2"
            else:
                raise ValueError(
                    f"Unsupported model backend for synthesis steps: {type(self.model)}"
                )

        # Pick a model for simple status messages or failure messages
        elif any(
            keyword in description
            for keyword in ["status", "message", "failure", "error", "inform the user"]
        ):
            if isinstance(self.model, OpenAIBackend):
                return "gpt-5-nano"
            elif isinstance(self.model, AzureOpenAIBackend):
                return "gpt-5-nano"
            elif isinstance(self.model, BedrockBackend):
                return "global.anthropic.claude-haiku-4-5-20251001-v1:0"
            elif isinstance(self.model, GCPVertexAIBackend):
                return "gemini-2.5-nano"
            elif isinstance(self.model, OllamaBackend):
                return "llama3.2"
            else:
                raise ValueError(
                    f"Unsupported model backend for status/failure messages: {type(self.model)}"
                )
        # Default to the most powerful model for any steps that don't match specific keywords,
        # as a safe fallback to ensure quality.
        if isinstance(self.model, OpenAIBackend):
            return "gpt-5.2"
        elif isinstance(self.model, AzureOpenAIBackend):
            return "gpt-5.2"
        elif isinstance(self.model, BedrockBackend):
            return "global.anthropic.claude-sonnet-4-6"
        elif isinstance(self.model, GCPVertexAIBackend):
            return "gemini-2.5-pro"
        elif isinstance(self.model, OllamaBackend):
            return "nemotron-3-nano"
        else:
            raise ValueError(f"Unsupported model backend: {type(self.model)}")

    async def _check_files(self, query: str) -> str:
        """Check current files for relevant information."""
        try:
            files_client = self.mcp_servers.get("file_handler")
            if not files_client:
                logger.error("File handler MCP server not available")
                return "No file context available."
            logger.info("Checking files for query: %s", query)

            # Gets all available files
            files_info = await files_client.call_tool("list_files", {})
            if not files_info or "files" not in files_info:
                logger.warning(
                    "File handler did not return a valid response: %s", files_info
                )
                return "No relevant files found."

        except Exception as e:
            logger.exception("Error checking files: %s", str(e))
            return f"File check failed: {str(e)}"

    async def _check_memory(self, query: str) -> str:
        """Check long-term memory for relevant information"""
        try:
            memory_client = self.mcp_servers.get("memory")
            if not memory_client:
                logger.error("Memory MCP server not available")
                return "No memory context available."

            logger.info("Checking memory for query: %s", query)
            result = await memory_client.call_tool(
                "recall_memories", {"query": query, "limit": 1000}
            )
            if result and "results" in result:
                context = "\n".join([f"- {r['content']}" for r in result["results"]])
                self.memory_context = context  # Store memory context for use in later steps without needing to query again
                return context
            return "No relevant memory found."
        except Exception as e:
            return f"Memory check failed: {str(e)}"

    async def _generate_plan(
        self, query: str, memory_context: str, user_feedback: str = None
    ) -> ResearchPlan:
        """Generate a research plan using the model"""
        system_prompt = """You are a research planning assistant. Given a research query and existing knowledge,
create a detailed, step-by-step research plan. Each step should be specific and actionable.

Format your response as a JSON object with the following structure:
{
  "goal": "Clear statement of the research goal",
  "steps": [
    {"id": 1, "description": "First step description"},
    {"id": 2, "description": "Second step description"},
    ...
  ]
}

Focus on:
1. Searching for credible sources
2. Scraping websites and analyzing content
3. Following relevant links for deeper information
4. Cross-referencing multiple sources
5. Identifying patterns and insights"""

        user_prompt = f"""Research Query: {query}

Existing Knowledge:
{memory_context}

Please create a comprehensive research plan."""

        # Add user feedback to the prompt if this is a plan modification based on feedback, to help the model
        # understand how to adjust the plan accordingly
        user_prompt += (
            f"\n\nUser Feedback on Previous Plan. Use this to inform your plan making decisions: {user_feedback}"
            if user_feedback
            else ""
        )

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        model = self._select_model_for_step("plan generation")
        logger.info(
            "Generating research plan for query: %s using model %s", query, model
        )
        response: ModelResponse = await self.model.generate(model, messages)

        # Parse the plan from the response
        try:
            # Extract JSON from response
            content = response.content
            # Handle cases where the model wraps JSON in code blocks
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            plan_data = json.loads(content)

            steps = [
                ResearchStep(
                    id=step["id"],
                    name=f"Step {step['id']}",
                    description=step["description"],
                    status=StepStatus.PENDING,
                )
                for step in plan_data["steps"]
            ]

            # Store the generated plan for later execution after approval
            plan = ResearchPlan(goal=plan_data["goal"], steps=steps)
            self._pending_plan = plan
            logger.info(
                "Generated research plan: %s", json.dumps(plan.to_dict(), indent=2)
            )
            return plan

        except Exception as e:
            logger.warning(
                "Failed to parse research plan, using fallback. Error: %s", str(e)
            )
            raise Exception(f"Failed to generate a valid research plan.{str(e)}")

    async def _execute_step(self, step: ResearchStep, query: str) -> str:
        """Execute a single research step using the model with tool calling

        Returns the result of the step execution, which will be stored and used for context in later steps and synthesis.
        """
        step.status = StepStatus.IN_PROGRESS

        # Get available tools
        tools = await self.mcp_servers.get_all_tools()

        # Build context from previous steps
        context = f"Original Query: {query}\n\n"
        if self.memory_context:
            context += f"Existing Knowledge:\n{self.memory_context}\n\n"

        prior_context = self._step_context.retrieve_relevant(step.description, top_k=3)
        if prior_context:
            context += f"Relevant Prior Research:\n{prior_context}\n\n"

        system_prompt = """You are a research assistant executing a specific research step. Use the available tools to 
        gather information, analyze content, and build knowledge. Be thorough and follow links when they contain 
        relevant information. Always cite your sources and cross-reference information."""

        user_prompt = f"""{context}

        Current Step: {step.description}

        Execute this step using the available tools. Provide a comprehensive summary of what you learned."""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        # Select model based on step complexity
        model = self._select_model_for_step(step)

        logger.info(
            "(Model=%s) Executing step %d: %s", model, step.id, step.description
        )

        # Iterative tool calling loop.
        # Loop until either the model stops calling tools or we reach the maximum number of iterations
        max_iterations = 10
        iteration = 0
        final_message = ""

        while iteration < max_iterations:
            response = await self.model.generate(model, messages, tools)

            # If no tool calls, we're done
            if not response.tool_calls:
                final_message = response.content
                break

            # Execute tool calls
            for tool_call in response.tool_calls:
                try:
                    # Add assistant message with tool call
                    messages.append(
                        Message(role="user", content=f"Calling tool: {tool_call.name}")
                    )

                    tool_result = await self.mcp_servers.call_tool(
                        tool_call.name, tool_call.parameters
                    )
                    if "error" in tool_result:
                        messages.append(
                            Message(
                                role="user",
                                content=f"Tool call {tool_call.name} failed with error: {tool_result['error']}",
                            )
                        )
                        continue  # Skip to next tool call or iteration

                    # Add tool result
                    messages.append(
                        Message(
                            role="user",
                            content=f"Tool result: {json.dumps(tool_result, indent=2)}",
                        )
                    )

                except Exception as e:
                    logger.exception("Error executing tool call: %s", str(e))
                    messages.append(
                        Message(role="user", content=f"Tool call failed: {str(e)}")
                    )
                    continue  # Continue with next tool call or iteration

            iteration += 1

        return final_message

    async def _synthesize_findings(
        self, query: str, step_results: List[str]
    ) -> SynthesisResult:
        """Synthesize all findings into insights and recommendations"""
        system_prompt = """You are a research synthesis expert. Given research findings,
create a comprehensive synthesis that includes:

1. Summary: A clear, concise summary of all findings
2. Key Insights: Novel insights derived from the research
3. Patterns: Patterns or trends identified across sources
4. Recommendations: Actionable recommendations based on the findings
5. Creative Applications: Innovative ways to apply this knowledge
6. Knowledge Gaps: Areas that need further research

Format your response as a JSON object with these keys.

Example:

{
  "summary": "Summary of findings...",
  "key_insights": ["Insight 1", "Insight 2"],
  "patterns": ["Pattern 1", "Pattern 2"],
  "recommendations": ["Recommendation 1", "Recommendation 2"],
  "creative_applications": ["Application 1", "Application 2"],
  "knowledge_gaps": ["Gap 1", "Gap 2"]
}

"""

        findings_text = "\n\n".join(
            [f"Finding {i + 1}:\n{result}" for i, result in enumerate(step_results)]
        )

        user_prompt = f"""Research Query: {query}

Research Findings:
{findings_text}

Please synthesize these findings."""

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]

        model = self._select_model_for_step("synthesis")
        response = await self.model.generate(model, messages)

        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            # Validate response object
            content_obj = json.loads(content)
            result = SynthesisResult(**content_obj)
            return result
        except Exception as e:
            logger.exception(
                "Failed to parse synthesis, using fallback. Error: %s", str(e)
            )
            # Fallback structure
            return SynthesisResult(
                summary=response.content,
                key_insights=[],
                patterns=[],
                recommendations=[],
                creative_applications=[],
                knowledge_gaps=[],
            )

    async def _save_to_memory(
        self,
        query: str,
        plan: ResearchPlan,
        step_results: List[str],
        synthesis: SynthesisResult,
    ) -> None:
        """Save research results to long-term memory"""
        try:
            memory_client = self.mcp_servers.get("memory")
            if not memory_client:
                return

            # Save the overall research session
            await memory_client.call_tool(
                "store_memory",
                {
                    "key": f"research_{query}",
                    "content": json.dumps(
                        {
                            "query": query,
                            "goal": plan.goal,
                            "findings": step_results,
                            "synthesis": synthesis.model_dump(),
                        }
                    ),
                    "metadata": {"type": "research_session", "query": query},
                },
            )

            # Save key insights as separate memories for better retrieval
            for i, insight in enumerate(synthesis.key_insights):
                await memory_client.call_tool(
                    "store_memory",
                    {
                        "key": f"insight_{query}_{i}",
                        "content": insight,
                        "metadata": {"type": "insight", "source_query": query},
                    },
                )
        except Exception as e:
            print(f"Failed to save to memory: {e}")

    async def _generate_final_document(
        self, query: str, synthesis: Dict[str, Any]
    ) -> str:
        """Generate a final research document based on the synthesis and save as an artifact"""
        system_prompt = """You are a research document generator."""

        user_prompt = f"""Create a comprehensive research report based on the  following synthesis of findings. 
        The report should be well-structured with sections for Summary, Key Insights, Patterns, Recommendations, 
        Creative Applications, and Knowledge Gaps.
        
        Research Query: {query}
        
        Synthesis of Findings: {json.dumps(synthesis, indent=2)}

        The output content should be in a file format suitable for sharing with others, such as a well-formatted 
        text document."""

        try:
            messages = [
                Message(role="system", content=system_prompt),
                Message(role="user", content=user_prompt),
            ]
            model = self._select_model_for_step("final document generation")
            response = await self.model.generate(model, messages)
            return response.content
        except Exception as e:
            logger.exception("Failed to generate final document: %s", str(e))
            return "Failed to generate final document."

    async def _save_artifact(self, file_name: str, content: str) -> str:
        """Save content as an artifact and return a URL (for simplicity, we save to a file)"""
        try:
            file_client = self.mcp_servers.get("file_handler")
            if not file_client:
                logger.error("File handler MCP server not available")
                return "Failed: File handler MCP server not available."

            tool_args = {
                "file_name": file_name,
                "content": content,
                "encoding": "utf-8",
                "mode": "write",
                "chunk_size": 1024 * 1024,  # 1MB
                "max_file_size": 10 * 1024 * 1024,  # 10MB
            }

            result = await file_client.call_tool("write_file", tool_args)
            if not result:
                logger.error("File handler did not return a result for tool call.")
                return "Failed to save artifact."
            if "file_url" in result:
                return result["file_url"]
            else:
                logger.error("File handler did not return a URL: %s", result)
                return "Failed to save artifact."

        except Exception as e:
            logger.exception("Failed to save artifact: %s", str(e))
            return "Failed to save artifact."


async def main():
    """Simple CLI for testing the ResearchAgent independently of the API server"""
    configs = Config()
    model_backend = create_model_backend(configs)

    # Initialize MCP registry and register servers
    mcp_registry = await create_mcp_registry(configs)

    # Create research agent
    agent = ResearchAgent(model_backend, mcp_registry)

    # Get query from user
    query = input("Enter your research query: ")

    # Generate plan and wait for approval
    print("Generating research plan...")
    async for plan in agent.generate_plan(query):
        print(f"Generated plan:\n{json.dumps(plan, indent=2)}\n")
        print("Proceed with executing the plan? (y/n)")
        if input().lower() == "y" or input().lower() == "yes":
            print("Executing approved plan...")
            async for result in agent.execute_approved_plan():
                print(f"Step Result: {json.dumps(result, indent=2)}")
            break
        else:
            print("Plan denied. You can modify the query and try again.")
            break

    # Display plan and ask for approval
    proceed = input("Do you want to proceed with executing this plan? (y/n): ")
    if proceed.lower() == "y" or proceed.lower() == "yes":
        print("Executing approved plan...")
        async for result in agent.execute_approved_plan():
            print(f"Step Result: {json.dumps(result, indent=2)}")
    else:
        print("Plan denied. You can modify the query and try again.")
        exit(0)

    # Synthesize results
    print("Synthesizing results...")
    async for synthesis in agent.synthesize_results():
        print(f"Synthesis Result: {json.dumps(synthesis, indent=2)}")

    print("Research process complete.")


if __name__ == "__main__":
    asyncio.run(main())
