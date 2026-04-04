"""
File reader and writer MCP server. This server provides tools for reading and writing files, supporting both simple and
streaming operations. It is designed to handle various file sizes and use cases, making it a versatile component for
file management tasks in the Research Agent ecosystem.

This server is meant to be run in a containerized environment to isolate arbitrary file writes from the host system,
and can be easily integrated with the Research Agent through its defined tools.
"""

import os
import logging
from typing import Generator, Any

from dotenv import load_dotenv

from mcp.server import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from tools.file_reader import read_file, read_file_simple, get_all_files
from tools.file_writer import write_file
from tools.file_transfer import upload_file, download_file
from tools.file_discovery import run_command, CommandRequest

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("FileHandlerMCP")

HOST_ADDR = os.getenv("HOST_ADDR", "0.0.0.0")
HOST_PORT = int(os.getenv("HOST_PORT", 9191))

# Configure output directory
OUTPUT_DIR = os.path.join(
    os.path.abspath(os.path.dirname(__file__)), "data", "documents"
)
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# Create the FastMCP server.
mcp = FastMCP(
    name="File Handler",
    host=HOST_ADDR,
    port=HOST_PORT,
    log_level="INFO",
)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Health check endpoint for Docker"""
    return JSONResponse({"status": "healthy", "service": "file-handler-server"})


@mcp.custom_route("/files", methods=["GET"])
async def list_uploaded_files(request: Request) -> JSONResponse:
    """Return a JSON array of all files currently in the data directory."""
    try:
        files = get_all_files(OUTPUT_DIR)
        return JSONResponse(files)
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/files/upload", methods=["POST"])
async def receive_uploaded_file(request: Request) -> JSONResponse:
    """Accept a multipart/form-data file upload and persist it to the data directory."""
    try:
        form = await request.form()
        uploaded = form.get("file")
        if uploaded is None:
            return JSONResponse({"error": "No file field in request"}, status_code=400)
        # Use basename only — never trust user-supplied paths for writes.
        filename = os.path.basename(getattr(uploaded, "filename", "") or "")
        if not filename:
            return JSONResponse(
                {"error": "Invalid or missing filename"}, status_code=400
            )
        file_path = os.path.join(OUTPUT_DIR, filename)
        content = await uploaded.read()
        with open(file_path, "wb") as f:
            f.write(content)
        logger.info(f"Saved uploaded file: {filename} ({len(content)} bytes)")
        return JSONResponse(
            {"filename": filename, "size": len(content), "status": "ready"}
        )
    except Exception as e:
        logger.error(f"Error receiving uploaded file: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.custom_route("/files/{filename}", methods=["DELETE"])
async def delete_uploaded_file(request: Request) -> JSONResponse:
    """Delete a previously uploaded file from the data directory."""
    filename = os.path.basename(request.path_params.get("filename", ""))
    if not filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    file_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(file_path):
        return JSONResponse({"error": "File not found"}, status_code=404)
    try:
        os.remove(file_path)
        logger.info(f"Deleted uploaded file: {filename}")
        return JSONResponse({"status": "deleted", "filename": filename})
    except Exception as e:
        logger.error(f"Error deleting file '{filename}': {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@mcp.tool(
    "list_files",
    description="""Recursively searches the output directory for files and returns a list of file paths relative to the 
    output directory. This tool is useful for discovering available files that can be read or processed by other tools.""",
)
def list_files_tool() -> list[dict]:
    logger.info(f"Listing files in output directory: {OUTPUT_DIR}")
    try:
        files = get_all_files(OUTPUT_DIR)
        logger.info(f"Found {len(files)} files in output directory.")
        return files
    except Exception as e:
        logger.error(f"Error listing files in output directory: {e}")
        raise


@mcp.tool(
    name="read_file",
    description="""
    Read the contents of a file. 
    This tool is designed for reading small to medium-sized files where loading the entire content into memory is feasible.
    """,
)
def read_file_tool(file_path: str) -> str:
    logger.info(f"Reading file: {file_path}")
    try:
        content = read_file_simple(file_path)
        logger.info(f"Successfully read file: {file_path}")
        return content
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        raise


@mcp.tool(
    name="streaming_read_file",
    description="""
    Stream the contents of a file in chunks. This tool is designed for reading large files without loading the entire file into memory at once.
    The tool yields chunks of the file content as they are read, allowing for processing of large files in a memory-efficient manner. 
    Each chunk is returned as a dictionary containing the chunk content and metadata (e.g., chunk number, total chunks).
    This tool is ideal for scenarios where the file size exceeds available memory or when processing can be done incrementally.
    """,
)
def streaming_read_file_tool(
    file_path: str = None,
    chunk_size: int = 8192,
    encoding: str = "utf-8",
    max_file_size: int = 10 * 1024 * 1024,
) -> Generator[dict[str, Any], None, None]:
    try:
        if not file_path:
            raise ValueError(
                "The 'file_path' parameter is required for streaming_read_file_tool."
            )

        inputs = {
            "file_path": file_path,
            "chunk_size": chunk_size,
            "encoding": encoding,
            "max_file_size": max_file_size,
        }
        for file_chunk in read_file(inputs):
            yield file_chunk
        logger.info(f"Finished streaming file: {file_path}")
    except Exception as e:
        logger.error(f"Error streaming file: {e}")
        raise


@mcp.tool(
    name="write_file",
    description="""
    Write content to a file. This tool supports different write modes (write, append, create) and can create parent 
    directories if needed.
    """,
)
def write_file_tool(
    file_name: str,
    content: str,
    encoding: str,
    mode: str,
    max_file_size: int,
    chunk_size: int,
) -> dict:
    try:
        results = []
        # NOTE: MCP servers make their inputs known to models, so having them explicitly spelled out as parameters
        # instead of a single dict input can help with model understanding and reduce errors in invocation formatting.
        write_file_input = {
            "file_name": file_name,
            "content": content,
            "encoding": encoding,
            "mode": mode,
            "create_dirs": True,
            "max_file_size": max_file_size,
            "chunk_size": chunk_size,
        }
        for result in write_file(write_file_input):
            results.append(result)
        if any(result.get("status") == "error" for result in results):
            logger.error(f"Error writing file: {results}")
            raise Exception(f"Error writing file: {results}")

        # Create URL for the written file to return in the response
        file_url = f"/files/{file_name}"
        logger.info("Finished writing file successfully.")
        return {"status": "success", "file_url": file_url}
    except Exception as e:
        logger.error(f"Error writing file: {e}")
        raise


@mcp.tool(
    name="upload_file",
    description="""Upload a file from the output directory to a specified destination URL. 
    Invocation should include 'file_path' (path to the file in the output directory) and 'upload_url' 
    (the destination URL to upload the file to).""",
)
def upload_file_tool(file_path: str, upload_url: str) -> None:
    try:
        if not file_path or not upload_url:
            raise ValueError(
                "Both 'file_path' and 'upload_url' must be provided in the invocation."
            )

        upload_file(upload_url, file_path)
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        raise


@mcp.tool(
    name="download_file",
    description="""Download a file from a specified URL and save it to the output directory.
    Invocation should include 'file_url' (the URL to download the file from) and 'headers' (optional dict of HTTP 
    headers to include in the download request).""",
)
def download_file_tool(file_url: str, headers: dict) -> str:
    try:
        if not file_url:
            raise ValueError("'file_url' must be provided in the invocation.")

        downloaded_file_path = download_file(file_url, OUTPUT_DIR, headers)
        return downloaded_file_path
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        raise


@mcp.tool(
    name="run_command",
    description="""
    Run a shell command on the server and return its output. 
    This tool only allows ready only commands for the use of file discovery and system information gathering.
    "The command is checked against an allowlist before execution, and any command whose base (first token) 
    is not in the allowlist will be rejected.
    
    If this tool is invoked, use the command and any args needed. The entire command string 
    is passed to the server and executed in a shell environment. The output (stdout, stderr, return code) is returned in the response.
    
    Allowed commands: 
        ls, cat, head, tail, pwd, find, du, df, stat, file, wc, grep, awk, sed, sort
        uniq, cut, tr, diff, echo, date, uptime, whoami, uname, ps, env, which, lsof
        
    Examples:
    - To list files in the output directory: "ls -la /app/data"
    - To print the first 10 lines of a file: "head -n 10 /app/data/myfile.txt" 
    - To find all .txt files: "find /app/data -type f -name '*.txt'"
    """,
)
def run_command_tool(command: str, timeout: int = 30) -> dict:
    """
    Execute a shell command and return its output.

    Only commands whose base (first token) appear in the server's allowlist
    will be executed. Everything else is rejected before any subprocess is spawned.

    Args:
        command: The shell command to execute.
        timeout: Maximum time in seconds to wait for the command (default: 30). This is to prevent long-running or hanging processes.
    """

    try:
        request = CommandRequest(command=command, timeout=timeout)
        result = run_command(request)
        return result.model_dump()
    except Exception as e:
        logger.error(f"Error running command '{command}': {e}")
        raise


if __name__ == "__main__":
    try:
        logger.info(f"Starting File Handler MCP Server on {HOST_ADDR}:{HOST_PORT}...")
        mcp.run(transport="streamable-http")
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
