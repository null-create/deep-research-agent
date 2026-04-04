"""
Streaming file reader and writer tool that can be served by an MCP server
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Callable, Generator, Any, Optional
import logging

from mcp.types import TextContent
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# Streaming Configuration
MAX_STREAMING_SIZE = 10 * 1024 * 1024  # 10MB - above this, we use true streaming
MAX_CONCAT_SIZE = 1 * 1024 * 1024  # 1MB - above this, warn about memory usage


class FileReadInput(BaseModel):
    path: str = Field(..., description="Absolute or relative path to the file")
    encoding: str = Field("utf-8", description="Text encoding to use when reading")
    chunk_size: int = Field(
        4096, description="Size of each chunk in bytes", ge=512, le=1024 * 1024
    )  # 512B to 1MB
    max_file_size: Optional[int] = Field(
        100 * 1024 * 1024, description="Maximum file size in bytes (default: 100MB)"
    )

    @field_validator("path")
    def validate_path(cls, v):
        if not v or not v.strip():
            raise ValueError("Path cannot be empty")
        return v.strip()


class FileReadOutput(BaseModel):
    path: str
    content: str
    total_chunks: int
    file_size: int


class FileChunk(BaseModel):
    """Represents a single streamed chunk of text."""

    chunk: str
    index: int
    eof: bool = False
    chunk_size: int = 0
    error: Optional[str] = None


class FileReaderError(Exception):
    """Custom exception for file reader errors."""

    pass


# --- Document text extractors ---


def _extract_pdf_text(file_path: Path) -> str:
    try:
        import pypdf
    except ImportError:
        raise FileReaderError(
            "pypdf is required to read PDF files. Install it with: pip install pypdf"
        )
    reader = pypdf.PdfReader(str(file_path))
    pages = [page.extract_text() for page in reader.pages]
    return "\n\n".join(p for p in pages if p and p.strip())


def _extract_docx_text(file_path: Path) -> str:
    try:
        import docx
    except ImportError:
        raise FileReaderError(
            "python-docx is required to read DOCX files. Install it with: pip install python-docx"
        )
    doc = docx.Document(str(file_path))
    return "\n\n".join(para.text for para in doc.paragraphs if para.text.strip())


def _extract_odt_text(file_path: Path) -> str:
    try:
        from odf import text as odf_text, teletype
        from odf.opendocument import load
    except ImportError:
        raise FileReaderError(
            "odfpy is required to read ODT files. Install it with: pip install odfpy"
        )
    doc = load(str(file_path))
    paragraphs = [teletype.extractText(p) for p in doc.getElementsByType(odf_text.P)]
    return "\n\n".join(p for p in paragraphs if p.strip())


# Maps file extensions to their text-extraction function.
_EXTRACTORS: dict[str, Callable[[Path], str]] = {
    ".pdf": _extract_pdf_text,
    ".docx": _extract_docx_text,
    ".odt": _extract_odt_text,
}


def get_all_files(directory: str | Path) -> list[dict]:
    """
    Recursively traverses a directory tree and compiles a list of all files
    with their absolute paths.

    Args:
        directory: The root directory to start traversal from.

    Returns:
        A list of dictionaries containing file information:
        - 'name': The file name
        - 'absolute_path': The absolute path to the file
        - 'relative_path': The path relative to the root directory
        - 'size_bytes': The file size in bytes

    Raises:
        NotADirectoryError: If the provided path is not a directory.
        FileNotFoundError: If the provided path does not exist.
    """
    root = Path(directory).resolve()

    if not root.exists():
        raise FileNotFoundError(f"Directory not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {root}")

    files = []

    for entry in root.iterdir():
        try:
            if entry.is_file():
                files.append(
                    {
                        "name": entry.name,
                        "absolute_path": str(entry.absolute()),
                        "relative_path": str(entry.relative_to(root)),
                        "size_bytes": entry.stat().st_size,
                    }
                )
            elif entry.is_dir():
                # Recursive call for subdirectories
                files.extend(get_all_files(entry))

        except PermissionError:
            logger.debug(f"Permission denied, skipping: {entry}")

    return files


def validate_file_access(path: str, max_file_size: Optional[int] = None) -> Path:
    """Validate file exists, is readable, and within size limits."""
    expanded_path = Path(path).expanduser().resolve()

    # Security: Ensure we're not accessing restricted paths
    # NOTE: Might want to add more sophisticated path validation here
    if not expanded_path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if not expanded_path.is_file():
        raise FileReaderError(f"Path is not a file: {path}")

    # Check file size
    file_size = expanded_path.stat().st_size
    if max_file_size and file_size > max_file_size:
        raise FileReaderError(
            f"File too large: {file_size} bytes (max: {max_file_size})"
        )

    return expanded_path


def stream_file_chunks(
    path: str,
    encoding: str = "utf-8",
    chunk_size: int = 4096,
    max_file_size: Optional[int] = None,
) -> Generator[FileChunk, None, None]:
    """Stream the file in chunks with proper error handling."""

    try:
        # Validate file first
        file_path = validate_file_access(path, max_file_size)

        logger.info(f"Starting to stream file: {file_path} (chunk_size: {chunk_size})")

        # For binary document formats, extract plain text first then stream from memory.
        suffix = file_path.suffix.lower()
        if suffix in _EXTRACTORS:
            try:
                extracted = _EXTRACTORS[suffix](file_path)
            except FileReaderError:
                raise
            except Exception as e:
                raise FileReaderError(
                    f"Failed to extract text from {suffix[1:].upper()} file: {e}"
                ) from e
            file_obj: Any = io.StringIO(extracted)
        else:
            file_obj = open(file_path, "r", encoding=encoding, buffering=chunk_size)

        idx = 0

        with file_obj as f:
            while True:
                try:
                    data = f.read(chunk_size)

                    if not data:
                        # End of file reached
                        yield FileChunk(chunk="", index=idx, eof=True, chunk_size=0)
                        logger.info(
                            f"Finished streaming file: {file_path} ({idx} chunks)"
                        )
                        break

                    yield FileChunk(
                        chunk=data, index=idx, eof=False, chunk_size=len(data)
                    )
                    idx += 1

                except UnicodeDecodeError as e:
                    # Handle encoding errors gracefully
                    error_msg = f"Encoding error at chunk {idx}: {str(e)}"
                    logger.warning(error_msg)
                    yield FileChunk(
                        chunk="", index=idx, eof=True, chunk_size=0, error=error_msg
                    )
                    break

                except Exception as e:
                    # Handle other read errors
                    error_msg = f"Read error at chunk {idx}: {str(e)}"
                    logger.error(error_msg)
                    yield FileChunk(
                        chunk="", index=idx, eof=True, chunk_size=0, error=error_msg
                    )
                    break

    except (FileNotFoundError, PermissionError, FileReaderError) as e:
        # Yield error chunk for initial file access errors
        error_msg = str(e)
        logger.error(f"File access error: {error_msg}")
        yield FileChunk(chunk="", index=0, eof=True, chunk_size=0, error=error_msg)

    except Exception as e:
        # Catch-all for unexpected errors
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg)
        yield FileChunk(chunk="", index=0, eof=True, chunk_size=0, error=error_msg)


def read_file(invocation: dict[str, str]) -> Generator[dict[str, Any], Any, None]:
    """Main file reader handler with improved error handling.

    Invocation should be a dict containing the input parameters, e.g.:
    {
        "input": {
            "path": "example.txt",
            "encoding": "utf-8",
            "chunk_size": 4096,
            "max_file_size": 104857600
        }
    }
    """
    try:
        payload = FileReadInput.model_validate(invocation.get("input", invocation))

        for file_chunk in stream_file_chunks(
            payload.path, payload.encoding, payload.chunk_size, payload.max_file_size
        ):
            yield file_chunk.model_dump()

            # If there's an error, stop streaming
            if file_chunk.error:
                break

    except Exception as e:
        # Handle validation errors
        error_msg = f"Input validation error: {str(e)}"
        logger.error(error_msg)
        yield FileChunk(
            chunk="", index=0, eof=True, chunk_size=0, error=error_msg
        ).model_dump()


def read_file_complete(
    path: str,
    encoding: str = "utf-8",
    chunk_size: int = 4096,
    max_file_size: Optional[int] = None,
) -> FileReadOutput:
    """Read entire file and return as a single output (for smaller files)."""

    chunks = []
    total_chunks = 0
    error = None

    for chunk_data in stream_file_chunks(path, encoding, chunk_size, max_file_size):
        chunk = FileChunk.model_validate(chunk_data)

        if chunk.error:
            error = chunk.error
            break

        if chunk.chunk:
            chunks.append(chunk.chunk)

        total_chunks += 1

        if chunk.eof:
            break

    if error:
        raise FileReaderError(error)

    content = "".join(chunks)
    file_path = validate_file_access(path)
    file_size = file_path.stat().st_size

    return FileReadOutput(
        path=str(file_path),
        content=content,
        total_chunks=total_chunks,
        file_size=file_size,
    )


# Convenience function for testing
def read_file_simple(path: str, encoding: str = "utf-8") -> str:
    """Simple synchronous file reader for small files."""
    try:
        result = read_file_complete(path, encoding)
        return result.content
    except FileReaderError as e:
        raise e
    except Exception as e:
        raise FileReaderError(f"Failed to read file: {str(e)}")


async def handle_concatenated_read(
    arguments: dict[str, Any], file_size: int
) -> list[TextContent]:
    """Handle small to medium files by concatenating all chunks."""

    try:
        chunks = []
        chunk_count = 0
        has_error = False
        error_msg = ""

        # Memory warning for larger files
        if file_size > MAX_CONCAT_SIZE:
            logger.warning(f"Reading large file ({file_size:,} bytes) into memory")

        # Stream and collect chunks
        for chunk_data in read_file({"input": arguments}):
            chunk = FileChunk.model_validate(chunk_data)
            chunk_count += 1

            if chunk.error:
                has_error = True
                error_msg = chunk.error
                break

            if chunk.chunk:
                chunks.append(chunk.chunk)

            if chunk.eof:
                break

        if has_error:
            return [TextContent(type="text", text=f"Error: {error_msg}")]

        # Concatenate and return
        content = "".join(chunks)

        logger.info(
            f"Successfully read file: {arguments.get('path')} "
            f"({len(content):,} characters, {chunk_count} chunks, {file_size:,} bytes)"
        )

        return [TextContent(type="text", text=content)]

    except Exception as e:
        error_msg = f"Error in concatenated read: {str(e)}"
        logger.error(error_msg)
        return [TextContent(type="text", text=f"Error: {error_msg}")]


async def handle_streaming_read(
    arguments: dict[str, Any], file_size: int
) -> list[TextContent]:
    """Handle large files with streaming approach."""

    try:
        chunk_size = arguments.get("chunk_size", 4096)
        estimated_chunks = (file_size + chunk_size - 1) // chunk_size

        # For very large files, we provide a streaming summary instead of full content
        logger.info(
            f"Streaming large file: {arguments.get('path')} "
            f"({file_size:,} bytes, ~{estimated_chunks:,} chunks)"
        )

        # Read first few chunks and last few chunks as a sample
        chunks = []
        chunks_read = 0
        first_chunks = []
        preview_len = 10  # limit preview length to 10 chunks
        content_preview = []
        has_error = False
        error_msg = ""

        # Read first 5 chunks for preview
        for chunk_data in read_file({"input": arguments}):
            chunk = FileChunk.model_validate(chunk_data)
            chunks_read += 1

            if chunk.error:
                has_error = True
                error_msg = chunk.error
                break

            if chunk.chunk and len(first_chunks) < 5:
                first_chunks.append(chunk.chunk)
                if chunks_read < preview_len:
                    content_preview.append(
                        f"Chunk {chunk.index}: {len(chunk.chunk)} chars"
                    )
                chunks.append(chunk.chunk)

            # NOTE: temp until we implement different approach to handle the streaming responses
            # if chunks_read >= 10 or chunk.eof:
            if chunk.eof:
                break

        if has_error:
            return [TextContent(type="text", text=f"Error: {error_msg}")]

        # Create streaming summary and include full file content in the message at the bottom for now.
        #
        # NOTE: will processing the file in chunks with the LLM be faster or slower? Inference calls would
        # probably be sequential, so it would probably bottleneck there if that's the case.
        preview_content = "".join(first_chunks)
        full_content = "".join(chunks)

        result = f"""Large File Streaming Summary:
File: {arguments.get('path')}
Size: {file_size:,} bytes
Estimated chunks: {estimated_chunks:,}
Chunks processed: {chunks_read}

Content Preview (first {len(first_chunks)} chunks):
{preview_content[:1000]}{"..." if len(preview_content) > 1000 else ""}

Chunk Details:
{chr(10).join(content_preview)}

Full Content:\n
{full_content}
"""

        return [TextContent(type="text", text=result)]

    except Exception as e:
        error_msg = f"Error in streaming read: {str(e)}"
        logger.error(error_msg)
        return [TextContent(type="text", text=f"Error: {error_msg}")]
