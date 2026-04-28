# Web Fetch MCP Server

A Model Context Protocol (MCP) server that provides web scraping capabilities using FastMCP. This server allows AI agents to fetch, parse, and extract content from web pages with various filtering and formatting options.

## Features

- **Complete page scraping** with optional link and image extraction
- **User-Agent rotation** across a pool of six modern browser strings (Chrome 123/124, Firefox 125, Safari 17) to reduce bot-detection fingerprinting
- **Concurrency cap** — at most 3 simultaneous requests via `asyncio.Semaphore` to prevent burst rate-limiting
- **Per-domain rate limiting** — 2-second minimum interval between successive requests to the same host
- **Automatic 429 retry** — retries once after the server's `Retry-After` delay (capped at 10 s) with a fresh User-Agent
- **50 KB output cap** — extracted page text is truncated at 50 000 characters to prevent log bloat
- **Clean text formatting** with whitespace normalization
- **Robust error handling** with detailed error messages

## Installation

1. Clone or download the code
2. Install the required dependencies:

```bash
pip install fastmcp httpx beautifulsoup4 python-dotenv
```

1. Create a `.env` file (optional) to configure the server:

```env
HOST_ADDR=0.0.0.0
HOST_PORT=9292
```

## Usage

### Starting the Server

Run the server directly:

```bash
python main.py
```

Run with `make` and `docker compose` (recommended)

```bash
# Build and start the container
make dev-compose

# Shut down and remove the container
make dev-down
```

The server will start on `0.0.0.0:9292` by default, or use the values from your `.env` file.

### Available Tools

#### `scrape_url`

Fetches and extracts the complete content of a web page.

**Parameters:**

- `url` (str): The URL of the web page to scrape
- `include_links` (bool, optional): Whether to include links found on the page (default: `false`)
- `include_images` (bool, optional): Whether to include image URLs found on the page (default: `false`)
- `clean_text` (bool, optional): Whether to clean and format the extracted text (default: `true`)

**Returns:** Plain text with the following structure:

```
URL: https://example.com/article
Status Code: 200
Content Type: text/html; charset=utf-8

--- PAGE CONTENT ---
<extracted text, truncated at 50 KB if needed>

--- LINKS ---          (if include_links=True)
Link text: https://example.com/other-page
...

--- IMAGES ---         (if include_images=True)
Alt text: https://example.com/image.jpg
...
```

Raises `ValueError` on HTTP errors or invalid URLs. HTTP 403 errors are not retried; HTTP 429 triggers one automatic retry after the server-specified delay.

**Example:**

```python
# Basic scraping
result = await scrape_url("https://example.com")

# Include links and images
result = await scrape_url(
    "https://example.com",
    include_links=True,
    include_images=True
)
```

## Configuration

### Environment Variables

- `HOST_ADDR`: Server bind address (default: `0.0.0.0`)
- `HOST_PORT`: Server port (default: `9292`)

### HTTP Client Settings

The server uses an `httpx.AsyncClient` with:

- 30-second timeout per request
- Automatic redirect following
- User-Agent rotated from a pool of six modern browser strings per request
- At most 3 concurrent in-flight requests (module-level `asyncio.Semaphore`)
- 2-second minimum interval between requests to the same domain
- Automatic 429 retry with `Retry-After` backoff (capped at 10 s)
- Extracted page text truncated at 50 000 characters

## Error Handling

The server provides detailed error messages for common issues:

- Invalid URL formats
- HTTP 4xx/5xx errors — 429 is retried once; all others raise immediately
- Network connectivity issues
- Parsing errors
