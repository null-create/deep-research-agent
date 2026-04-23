import os
from typing import Optional, Literal

from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()


class Config(BaseModel):
    """Global configuration for the research agent"""

    # ────────── Agent mode (research, chat, or self-optimization)
    agent_mode: Literal["research", "chat", "self-optimization"] = Field(
        default_factory=lambda: os.getenv("AGENT_MODE", "research")
    )

    # ───────── Logging Configuration. 20=INFO by default
    log_level: int = Field(default_factory=lambda: int(os.getenv("LOG_LEVEL", 20)))

    # ───────── Agent/LLM Configuration

    # Maximum number of tool-call iterations the SearchAgent is allowed per
    # research step before it must return its findings.  Higher values allow
    # deeper investigation but increase latency and token spend.
    max_iterations: int = Field(
        default_factory=lambda: int(os.getenv("MAX_ITERATIONS", "5"))
    )

    # Sampling temperature passed to the LLM.  Lower values (e.g. 0.2) produce
    # more deterministic, focused outputs; higher values (e.g. 0.9) increase
    # creativity and variation.  0.7 is a balanced default for research tasks.
    default_temperature: float = Field(
        default_factory=lambda: float(os.getenv("DEFAULT_TEMPERATURE", "0.7"))
    )

    # Nucleus sampling threshold.  The model considers only the smallest set of
    # tokens whose cumulative probability exceeds this value.  Works in tandem
    # with temperature; 0.9 is a safe default that retains most of the
    # probability mass while trimming the long tail.
    top_p: float = Field(default_factory=lambda: float(os.getenv("TOP_P", "0.9")))

    # Maximum number of tokens the LLM may generate in a single response.
    # Increase for long-form synthesis or report drafting; decrease to reduce
    # cost on cheaper, faster models used in early pipeline stages.
    max_tokens: int = Field(
        default_factory=lambda: int(os.getenv("MAX_TOKENS", "4096"))
    )

    # ────────── Concurrency Configuration
    # Maximum number of worker threads for concurrent execution of research sessions.
    # These are the number of steps that can be executed concurrently per research session,
    # not to be confused with max_concurrent_sessions which limits the number of
    # sessions that can run concurrently.
    # Set to 0 to disable multithreading (fully sequential execution).
    max_workers: int = Field(
        default_factory=lambda: int(os.getenv("MAX_WORKERS", "10"))
    )

    # Maximum number of research sessions that can run their execute+synthesize
    # pipeline simultaneously.  Sessions beyond this limit wait for a slot to
    # open.  Prevents OOM under heavy benchmark loads.  Set to 0 to disable
    # the limit (unlimited concurrency).
    max_concurrent_sessions: int = Field(
        default_factory=lambda: int(os.getenv("MAX_CONCURRENT_SESSIONS", "10"))
    )

    # ───────── Research Configuration

    # Maximum number of sources the SearchAgent will collect per individual
    # research query before moving on.  Higher values improve coverage at
    # the cost of longer search loops.
    max_sources_per_query: int = Field(
        default_factory=lambda: int(os.getenv("MAX_SOURCES_PER_QUERY", "5"))
    )

    # Maximum link-follow depth used by the deep-scrape crawler.  At depth 1
    # only the target page is fetched; at depth 2 one level of outbound links
    # is also scraped.  Keep low to avoid runaway crawls.
    max_depth: int = Field(default_factory=lambda: int(os.getenv("MAX_DEPTH", "2")))

    # When True, plan steps that share the same parallel_group are dispatched
    # concurrently via asyncio.gather.  Set to False to force fully sequential
    # step execution (useful for debugging or resource-constrained environments).
    enable_parallel_execution: bool = Field(
        default_factory=lambda: os.getenv("ENABLE_PARALLEL_EXECUTION", "True").lower()
        == "true"
    )

    # Maximum number of times the LoopAgent may flag contradictions and route
    # a step back for targeted re-investigation before synthesis is forced with
    # the best available findings.  Higher values improve accuracy but increase
    # latency and token usage.
    max_qa_retries: int = Field(
        default_factory=lambda: int(os.getenv("MAX_QA_RETRIES", "2"))
    )

    # ───────── RAG Retrieval Configuration

    # Number of top-k chunks retrieved from SearchResultStore per RAG query.
    # The RAG store is the sole context-management mechanism — no hard character
    # caps are applied anywhere in the pipeline.
    analyst_top_k: int = Field(
        default_factory=lambda: int(os.getenv("ANALYST_TOP_K", "10"))
    )

    # Maximum characters of a single tool result that are injected into the
    # SearchAgent's execution_messages sliding window.  Full content is preserved
    # in the SearchResultStore RAG store; only a short audit trail is kept in the
    # LLM message history to avoid context-window exhaustion.
    max_tool_result_chars_in_message: int = Field(
        default_factory=lambda: int(
            os.getenv("MAX_TOOL_RESULT_CHARS_IN_MESSAGE", "5000")
        )
    )

    # Maximum number of messages retained in the SearchAgent's execution_messages
    # sliding window between tool-loop iterations.  Older entries are evicted once
    # this limit is exceeded; the full content remains in the RAG store.
    # 8 messages ≈ 4 tool-call / result pairs.
    max_search_history_messages: int = Field(
        default_factory=lambda: int(os.getenv("MAX_SEARCH_HISTORY_MESSAGES", "8"))
    )

    # Maximum characters of raw search_output injected into the AnalystAgent when
    # the SearchResultStore has no chunks yet for this step (e.g. the very first
    # iteration before any tool results have been ingested).  This is a safety net;
    # the RAG store with in-process cosine ranking is the primary path.
    max_analyst_fallback_chars: int = Field(
        default_factory=lambda: int(os.getenv("MAX_ANALYST_FALLBACK_CHARS", "8000"))
    )

    # ───────── Hierarchical Distillation Configuration

    # Maximum characters the distillation filter preserves from a raw tool result.
    # ~2 000 chars ≈ ~500 tokens — large enough to keep a meaningful snippet,
    # small enough to avoid context bloat before chunking into the RAG store.
    distill_max_chars: int = Field(
        default_factory=lambda: int(os.getenv("DISTILL_MAX_CHARS", "4000"))
    )

    # Maximum characters for a per-step summary bullet generated after each step's
    # QA pass.  These summaries form the Orchestrator's "active context" (the
    # CEO's weekly briefing) and are the sole input to the Outline Phase of
    # multi-pass synthesis.
    step_summary_max_chars: int = Field(
        default_factory=lambda: int(os.getenv("STEP_SUMMARY_MAX_CHARS", "1500"))
    )

    # Number of top-k RAG chunks injected per report section during the
    # Section-Drafting phase of multi-pass synthesis.  A targeted RAG query
    # is issued per section so only relevant evidence is in context.
    section_draft_top_k: int = Field(
        default_factory=lambda: int(os.getenv("SECTION_DRAFT_TOP_K", "10"))
    )

    # ───────── Embeddings Configuration

    # Whether in-process sentence-transformer embeddings are enabled.  Set to
    # false on memory-constrained hosts; the SearchResultStore will fall back
    # to insertion-order ranking.
    embeddings_enabled: bool = Field(
        default_factory=lambda: os.getenv("EMBEDDINGS_ENABLED", "true").lower()
        not in ("0", "false", "no")
    )

    # Sentence-transformer model identifier (HuggingFace hub or local path).
    embeddings_model: str = Field(
        default_factory=lambda: os.getenv("EMBEDDINGS_MODEL", "all-MiniLM-L6-v2")
    )

    # Maximum number of concurrent encode() threads.  Caps CPU usage so that
    # a burst of parallel requests does not saturate all cores and stall uvicorn.
    max_embedding_workers: int = Field(
        default_factory=lambda: int(os.getenv("MAX_EMBEDDING_WORKERS", "2"))
    )

    # ───────── Long-term Memory (Neo4j)

    # Bolt URI for the Neo4j instance backing the long-term memory store.
    neo4j_uri: str = Field(
        default_factory=lambda: os.getenv("NEO4J_URI", "bolt://localhost:7687")
    )

    # Neo4j authentication credentials.
    neo4j_user: str = Field(default_factory=lambda: os.getenv("NEO4J_USER", "neo4j"))
    neo4j_password: str = Field(
        default_factory=lambda: os.getenv("NEO4J_PASSWORD", "research_pass")
    )

    # Neo4j database name (use "neo4j" for Community Edition).
    neo4j_database: str = Field(
        default_factory=lambda: os.getenv("NEO4J_DATABASE", "neo4j")
    )

    # Dimensionality of the embedding vectors stored in Neo4j vector indexes.
    # Must match the output size of the sentence-transformer model (384 for
    # all-MiniLM-L6-v2).
    neo4j_embedding_dimensions: int = Field(
        default_factory=lambda: int(os.getenv("NEO4J_EMBEDDING_DIMENSIONS", "384"))
    )

    # Half-life in days for confidence decay on RELATES_TO edges.  A
    # relationship not re-confirmed within this window loses half its
    # confidence.  Set to 0 to disable decay.
    confidence_decay_half_life: int = Field(
        default_factory=lambda: int(os.getenv("CONFIDENCE_DECAY_HALF_LIFE", "30"))
    )

    # Minimum number of new graph entities+relationships (combined) that must
    # be written in a session before community detection is triggered at the
    # end of synthesis.  Prevents redundant LLM calls when a session produces
    # little new graph data.
    graph_community_min_mutations: int = Field(
        default_factory=lambda: int(os.getenv("GRAPH_COMMUNITY_MIN_MUTATIONS", "3"))
    )

    # ───────── MCP Servers
    # Each MCP server exposes tools over the Model Context Protocol.  The URL
    # must point to the server's /mcp endpoint.  Optional API keys are forwarded
    # as Bearer tokens for authenticated deployments.

    # Web search server (port 9393): web_search, image_search, video_search,
    # search_wikipedia, search_github, get_search_suggestions, search_arxiv,
    # search_semantic_scholar.
    search_server_url: str = Field(
        default_factory=lambda: os.getenv(
            "SEARCH_SERVER_URL", "http://localhost:9393/mcp"
        )
    )
    search_server_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("SEARCH_SERVER_API_KEY")
    )

    # Web scraping server (port 9292): scrape_url (content extraction, metadata,
    # boilerplate removal).
    scraper_server_url: str = Field(
        default_factory=lambda: os.getenv(
            "SCRAPER_SERVER_URL", "http://localhost:9292/mcp"
        )
    )
    scraper_server_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("SCRAPER_SERVER_API_KEY")
    )

    # File handler server (port 9191): list_files, read_file, write_file,
    # upload_file, download_file.  Also serves browser-facing HTTP upload routes.
    file_server_url: str = Field(
        default_factory=lambda: os.getenv(
            "FILE_SERVER_URL", "http://localhost:9191/mcp"
        )
    )
    file_server_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("FILE_SERVER_API_KEY")
    )

    # ───────── Model Backend Configuration

    # Which LLM provider to use.  Determines which backend class is instantiated
    # in model_backend.py.  Valid values: "openai", "azure", "bedrock", "gcp",
    # "ollama", "huggingface".
    model_backend: str = Field(
        default_factory=lambda: os.getenv("MODEL_BACKEND", "ollama")
    )

    # ── OpenAI ──

    # API key for the OpenAI platform.  Required when model_backend="openai".
    openai_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY")
    )
    # Default model used when no heavy/light distinction applies.
    openai_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.2")
    )
    # Model used for planning, synthesis, and report generation ("heavy" tasks).
    openai_heavy_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_HEAVY_MODEL", "gpt-5.2")
    )
    # Model used for search execution and analyst extraction ("light" tasks).
    openai_light_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_LIGHT_MODEL", "gpt-5-nano")
    )
    # Base URL for the OpenAI-compatible API.  Override for proxies or
    # self-hosted endpoints that implement the OpenAI chat completions API.
    openai_base_url: str = Field(
        default_factory=lambda: os.getenv(
            "OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
    )

    # ── Azure OpenAI ──

    # Azure OpenAI resource endpoint (e.g. https://<resource>.openai.azure.com).
    azure_endpoint: Optional[str] = Field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT")
    )
    # API key for the Azure OpenAI resource.
    azure_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_API_KEY")
    )
    # Azure OpenAI API version string.  Must match a version supported by
    # the deployed model.
    azure_api_version: str = Field(
        default_factory=lambda: os.getenv("AZURE_API_VERSION", "2024-02-15-preview")
    )
    # Azure deployment name.  Maps to a specific model deployment within the
    # Azure OpenAI resource.
    azure_deployment_name: Optional[str] = Field(
        default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT")
    )
    # Model used for heavy tasks (planning, synthesis, report generation).
    azure_heavy_model: str = Field(
        default_factory=lambda: os.getenv("AZURE_HEAVY_MODEL", "gpt-5.2")
    )
    # Model used for light tasks (search execution, analyst extraction).
    azure_light_model: str = Field(
        default_factory=lambda: os.getenv("AZURE_LIGHT_MODEL", "gpt-5-nano")
    )

    # ── AWS Bedrock ──
    # Two backends are available:
    #   "aws"     – OpenAI-compatible gateway (requires AWS_BASE_URL + AWS_API_KEY)
    #   "bedrock" – Native boto3 bedrock-runtime (requires AWS_REGION; credentials
    #               are resolved via the standard boto3 chain: env vars,
    #               ~/.aws/credentials, IAM instance role, etc.)

    # Default Bedrock model identifier.
    aws_model: str = Field(
        default_factory=lambda: os.getenv(
            "AWS_MODEL", "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
        )
    )
    # Model used for heavy tasks (planning, synthesis, report generation).
    aws_heavy_model: str = Field(
        default_factory=lambda: os.getenv(
            "AWS_HEAVY_MODEL", "global.anthropic.claude-sonnet-4-6"
        )
    )
    # Model used for light tasks (search execution, analyst extraction).
    aws_light_model: str = Field(
        default_factory=lambda: os.getenv(
            "AWS_LIGHT_MODEL", "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        )
    )
    # OpenAI-compatible gateway URL for Bedrock access.
    aws_base_url: Optional[str] = Field(
        default_factory=lambda: os.getenv("AWS_BASE_URL")
    )
    # API key / token for the Bedrock gateway.
    aws_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("AWS_API_KEY"))
    # AWS region for native boto3 Bedrock access (MODEL_BACKEND=bedrock).
    aws_region: str = Field(
        default_factory=lambda: os.getenv("AWS_REGION", "us-east-1")
    )

    # ── GCP Vertex AI ──

    # Vertex AI endpoint URL.  Region is embedded in the URL (e.g.
    # us-central1).  Uses the OpenAI-compatible chat completions API.
    gcp_base_url: str = Field(
        default_factory=lambda: os.getenv(
            "GCP_ENDPOINT", "https://us-central1-aiplatform.googleapis.com/v1"
        )
    )
    # Default GCP model identifier.
    gcp_model: str = Field(
        default_factory=lambda: os.getenv("GCP_MODEL", "gemini-2.5-pro")
    )
    # Model used for heavy tasks (planning, synthesis, report generation).
    gcp_heavy_model: str = Field(
        default_factory=lambda: os.getenv("GCP_HEAVY_MODEL", "gemini-2.5-pro")
    )
    # Model used for light tasks (search execution, analyst extraction).
    gcp_light_model: str = Field(
        default_factory=lambda: os.getenv("GCP_LIGHT_MODEL", "gemini-2.5-flash")
    )
    # API key for GCP Vertex AI.
    gcp_api_key: Optional[str] = Field(default_factory=lambda: os.getenv("GCP_API_KEY"))

    # ── Ollama ──
    # Local inference via the Ollama HTTP API.  No API key needed.

    # Base URL for the Ollama server.
    ollama_base_url: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    # Default Ollama model (used when no heavy/light distinction applies).
    ollama_model: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "nemotron-3-nano")
    )
    # Model used for heavy tasks (planning, synthesis, report generation).
    ollama_heavy_model: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_HEAVY_MODEL", "nemotron-3-nano")
    )
    # Model used for light tasks (search execution, analyst extraction).
    ollama_light_model: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_LIGHT_MODEL", "llama3.2:3b")
    )

    # ── HuggingFace ──
    # HuggingFace Text Generation Inference (TGI) or Inference Endpoints.

    # Base URL for the HuggingFace inference server.
    huggingface_base_url: str = Field(
        default_factory=lambda: os.getenv(
            "HUGGINGFACE_BASE_URL", "http://localhost:8080"
        )
    )
    # Model identifier on the HuggingFace Hub (e.g. "meta-llama/Llama-3-8B").
    huggingface_model: Optional[str] = Field(
        default_factory=lambda: os.getenv("HUGGINGFACE_MODEL")
    )
    # API token for HuggingFace Inference Endpoints (optional for local TGI).
    huggingface_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("HUGGINGFACE_API_KEY")
    )

    # ── Anthropic ──
    # Direct Anthropic API (claude-* models).  Uses anthropic[aiohttp] SDK.

    # API key for the Anthropic platform.  Required when model_backend="anthropic".
    anthropic_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_API_KEY")
    )
    # Optional custom base URL (e.g. for proxies).  Defaults to the official API.
    anthropic_base_url: Optional[str] = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_BASE_URL") or None
    )
    # Default model identifier.
    anthropic_model: str = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")
    )
    # Model used for heavy tasks (planning, synthesis, report generation).
    anthropic_heavy_model: str = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_HEAVY_MODEL", "claude-opus-4-7")
    )
    # Model used for light tasks (search execution, analyst extraction).
    anthropic_light_model: str = Field(
        default_factory=lambda: os.getenv("ANTHROPIC_LIGHT_MODEL", "claude-haiku-4-5")
    )

    # ───────── Per-agent model overrides
    # When set, these override the heavy/light model selected by _select_model()
    # for the corresponding agent role.  Leave unset to use the backend defaults.

    # Override model for the Root (Orchestrator) agent — plans research steps.
    root_model_override: Optional[str] = Field(
        default_factory=lambda: os.getenv("ROOT_MODEL_OVERRIDE") or None
    )
    # Override model for the SearchAgent — executes tool-based retrieval.
    search_model_override: Optional[str] = Field(
        default_factory=lambda: os.getenv("SEARCH_MODEL_OVERRIDE") or None
    )
    # Override model for the AnalystAgent — extracts claims and tensions.
    analyst_model_override: Optional[str] = Field(
        default_factory=lambda: os.getenv("ANALYST_MODEL_OVERRIDE") or None
    )
    # Override model for the LoopAgent (QA) — detects contradictions.
    qa_model_override: Optional[str] = Field(
        default_factory=lambda: os.getenv("QA_MODEL_OVERRIDE") or None
    )

    # ───────── Per-agent sampling parameters
    # These are passed through to the LLM generate() calls for each agent role.
    # Each defaults to the global value (DEFAULT_TEMPERATURE, TOP_P, MAX_TOKENS)
    # but can be overridden per agent via environment variables.
    # Note: some backends (Bedrock, Azure, GCP) may ignore temperature/top_p
    # depending on the model — check your provider's documentation.

    # Sampling temperature per agent.  Lower = more deterministic.
    root_temperature: float = Field(
        default_factory=lambda: float(
            os.getenv("ROOT_TEMPERATURE", os.getenv("DEFAULT_TEMPERATURE", "0.7"))
        )
    )
    search_temperature: float = Field(
        default_factory=lambda: float(
            os.getenv("SEARCH_TEMPERATURE", os.getenv("DEFAULT_TEMPERATURE", "0.7"))
        )
    )
    analyst_temperature: float = Field(
        default_factory=lambda: float(
            os.getenv("ANALYST_TEMPERATURE", os.getenv("DEFAULT_TEMPERATURE", "0.7"))
        )
    )
    qa_temperature: float = Field(
        default_factory=lambda: float(
            os.getenv("QA_TEMPERATURE", os.getenv("DEFAULT_TEMPERATURE", "0.7"))
        )
    )

    # Nucleus sampling threshold per agent.  See top_p above for semantics.
    root_top_p: float = Field(
        default_factory=lambda: float(
            os.getenv("ROOT_TOP_P", os.getenv("TOP_P", "0.9"))
        )
    )
    search_top_p: float = Field(
        default_factory=lambda: float(
            os.getenv("SEARCH_TOP_P", os.getenv("TOP_P", "0.9"))
        )
    )
    analyst_top_p: float = Field(
        default_factory=lambda: float(
            os.getenv("ANALYST_TOP_P", os.getenv("TOP_P", "0.9"))
        )
    )
    qa_top_p: float = Field(
        default_factory=lambda: float(os.getenv("QA_TOP_P", os.getenv("TOP_P", "0.9")))
    )

    # Maximum generation tokens per agent.  See max_tokens above for semantics.
    root_max_tokens: int = Field(
        default_factory=lambda: int(
            os.getenv("ROOT_MAX_TOKENS", os.getenv("MAX_TOKENS", "4096"))
        )
    )
    search_max_tokens: int = Field(
        default_factory=lambda: int(
            os.getenv("SEARCH_MAX_TOKENS", os.getenv("MAX_TOKENS", "4096"))
        )
    )
    analyst_max_tokens: int = Field(
        default_factory=lambda: int(
            os.getenv("ANALYST_MAX_TOKENS", os.getenv("MAX_TOKENS", "4096"))
        )
    )
    qa_max_tokens: int = Field(
        default_factory=lambda: int(
            os.getenv("QA_MAX_TOKENS", os.getenv("MAX_TOKENS", "4096"))
        )
    )
