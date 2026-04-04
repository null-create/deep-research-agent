# Web Search MCP Server

A comprehensive web search server built with FastMCP that provides unified access to multiple search backends without requiring API keys. This server offers various search capabilities including web search, news, images, videos, and more through different providers.

## Features

- **Multiple Search Backends**: DuckDuckGo, Wikipedia, and GitHub
- **Diverse Search Types**: Web search, images, videos, Wikipedia articles, and GitHub repositories
- **No API Keys Required**: DuckDuckGo and Wikipedia work without authentication; GitHub optionally accepts a `GITHUB_TOKEN` to raise its rate limit
- **User-Agent rotation** across a pool of six modern browser strings to reduce bot-detection fingerprinting
- **Non-blocking execution**: all tool handlers run via `asyncio.to_thread` to avoid blocking the FastMCP event loop under concurrent orchestrator load
- **Search Suggestions**: Get search suggestions for query completion
- **Flexible Configuration**: Customizable parameters for each search type
- **HTTP Transport**: RESTful API interface via FastMCP

## Available Tools

### 1. `web_search`

Search the web using DuckDuckGo search engine.

**Parameters:**

- `query` (str): Search query string
- `backend` (str): Search backend (`duckduckgo`) - default: `duckduckgo`
- `max_results` (int): Maximum results to return (1-50) - default: 10
- `region` (str): Search region code - default: `wt-wt`
- `safesearch` (str): Safe search setting (`strict`, `moderate`, `off`) - default: `moderate`

### 2. `image_search`

Search for images using DuckDuckGo.

**Parameters:**

- `query` (str): Image search query
- `max_results` (int): Maximum images to return (1-50) - default: 10
- `size` (str): Image size (`Small`, `Medium`, `Large`, `Wallpaper`) - default: `Medium`
- `type_image` (str): Image type (`photo`, `clipart`, `gif`, `transparent`, `line`) - default: `photo`
- `region` (str): Search region code - default: `wt-wt`

### 3. `video_search`

Search for videos using DuckDuckGo.

**Parameters:**

- `query` (str): Video search query
- `max_results` (int): Maximum videos to return (1-50) - default: 10
- `duration` (str): Video duration (`Short`, `Medium`, `Long`) - default: `Medium`
- `resolution` (str): Video resolution (`High`, `Standard`) - default: `High`
- `region` (str): Search region code - default: `wt-wt`

### 4. `search_wikipedia`

Search Wikipedia articles for information.

**Parameters:**

- `query` (str): Wikipedia search query
- `max_results` (int): Maximum articles to return (1-50) - default: 10

### 5. `search_github`

Search GitHub repositories by name, description, or topic. Results are sorted by star count descending. If `GITHUB_TOKEN` is set in the environment, it is sent as a `Bearer` token, raising the rate limit from 60 req/hr (unauthenticated) to 5 000 req/hr.

**Parameters:**

- `query` (str): GitHub search query
- `max_results` (int): Maximum repositories to return (1-50) - default: 10

### 6. `get_search_suggestions`

Get search suggestions for a query to help with query completion.

**Parameters:**

- `query` (str): Partial search query
- `region` (str): Search region code - default: `wt-wt`

## Installation

### Dependencies

- **fastmcp**: MCP server framework
- **requests**: HTTP library for API calls
- **duckduckgo-search (ddgs)**: DuckDuckGo search API
- **wikipedia**: Wikipedia API wrapper
- **make** (For local development)
- **docker** (For local development)

### Prerequisites

Install dependencies with

```bash
python -m venv venv
source ./venv/bin/activate
pip install fastmcp requests duckduckgo-search wikipedia
```

Or

```bash
make init
```

## Configuration

The server can be configured using environment variables:

- `HOST_PORT`: Server port (default: `9393`)
- `HOST_ADDRESS`: Server host address (default: `0.0.0.0`)
- `GITHUB_TOKEN`: Optional GitHub personal access token. When set, included as `Authorization: Bearer` on all GitHub API requests, raising the rate limit from 60 req/hr to 5 000 req/hr.

## Usage

### Starting the Server

```bash
python server.py
```

Or run with `make` and `docker compose` (recommended)

```bash
# Start the container
make dev-compose
# Stop and remove the container
make dev-down
```

The server will start on `http://0.0.0.0:9393` by default.

### Example API Calls

**Web Search:**

```json
{
  "tool": "web_search",
  "arguments": {
    "query": "python programming",
    "max_results": 5,
    "safesearch": "moderate"
  }
}
```

**Image Search:**

```json
{
  "tool": "image_search",
  "arguments": {
    "query": "cute cats",
    "max_results": 10000,
    "size": "Large"
  }
}
```

**Wikipedia Search:**

```json
{
  "tool": "search_wikipedia",
  "arguments": {
    "query": "artificial intelligence",
    "max_results": 5
  }
}
```

**GitHub Search:**

```json
{
  "tool": "search_github",
  "arguments": {
    "query": "machine learning python",
    "max_results": 10
  }
}
```

**Search Suggestions:**

```json
{
  "tool": "get_search_suggestions",
  "arguments": {
    "query": "python web",
    "region": "us-en"
  }
}
```

## Response Format

### Web Search Response

```json
{
  "success": true,
  "backend": "duckduckgo",
  "query": "search query",
  "results_count": 5,
  "results": [
    {
      "title": "Result Title",
      "url": "https://example.com",
      "snippet": "Result description...",
      "source": "DuckDuckGo"
    }
  ]
}
```

### Image Search Response

```json
{
  "success": true,
  "query": "search query",
  "results_count": 10,
  "results": [
    {
      "title": "Image Title",
      "url": "https://example.com/image.jpg",
      "thumbnail": "https://example.com/thumb.jpg",
      "source": "DuckDuckGo Images",
      "width": "800",
      "height": "600"
    }
  ]
}
```

### Video Search Response

```json
{
  "success": true,
  "query": "search query",
  "results_count": 10,
  "results": [
    {
      "title": "Video Title",
      "url": "https://example.com/video",
      "thumbnail": "https://example.com/thumb.jpg",
      "duration": "5:30",
      "source": "YouTube",
      "published": "2023-01-01"
    }
  ]
}
```

### GitHub Search Response

```json
{
  "success": true,
  "query": "search query",
  "results_count": 10,
  "results": [
    {
      "title": "user/repository",
      "url": "https://github.com/user/repository",
      "snippet": "Repository description",
      "source": "GitHub",
      "stars": 1250,
      "language": "Python",
      "updated": "2023-12-01T10:00:00Z"
    }
  ]
}
```

### Search Suggestions Response

```json
{
  "success": true,
  "query": "partial query",
  "suggestions_count": 8,
  "suggestions": [
    "partial query completion 1",
    "partial query completion 2"
  ]
}
```

## Error Handling

The server includes comprehensive error handling:

- Input validation and sanitization
- Graceful fallback for failed backends
- Detailed error messages in responses
- Logging for debugging purposes

Error responses follow this format:

```json
{
  "success": false,
  "error": "Error description",
  "query": "original query"
}
```

## Project Structure

```
.
├── __init__.py
├── .env.example
├── .github
│   └── workflows
│       └── publish.yml
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── infra
│   ├── build.tf
│   ├── configs.tfvars.example
│   ├── main.tf
│   ├── outputs.tf
│   ├── provider.tf
│   ├── rgroup.tf
│   └── variables.tf
├── Makefile
├── README.md
├── requirements.txt
├── search_backends.py
├── server.json
└── server.py
```

## Supported Regions

Common region codes:

- `wt-wt`: Worldwide
- `us-en`: United States (English)
- `uk-en`: United Kingdom (English)
- `de-de`: Germany (German)
- `fr-fr`: France (French)

## Search Backends

### DuckDuckGo

- **Web Search**: General web search results
- **Image Search**: Images with size and type filtering
- **Video Search**: Videos with duration and resolution filtering
- **Suggestions**: Query completion suggestions

### Wikipedia

- **Article Search**: Wikipedia articles with summaries
- **Disambiguation Handling**: Automatically handles disambiguation pages
- **Content Extraction**: Returns article summaries and full URLs

### GitHub

- **Repository Search**: Public GitHub repositories
- **Sorting**: Results sorted by star count (descending)
- **Metadata**: Includes stars, language, and last updated information
- **Rate Limiting**: Subject to GitHub's unauthenticated API limits

## Limitations

- **Rate Limits**: Subject to provider rate limits
- **No Authentication**: GitHub search limited to public repositories only
- **Content Filtering**: Depends on provider's filtering capabilities
- **Availability**: Dependent on third-party service availability
- **Wikipedia Disambiguation**: May not handle all disambiguation cases perfectly
- **GitHub API**: Limited to 60 requests per hour for unauthenticated requests
