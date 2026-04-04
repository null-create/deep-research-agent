"""
Streaming file writer tool that can be served by an MCP server
"""

from __future__ import annotations

import os
import logging
import tempfile
from pathlib import Path
from typing import Generator, Any, Optional

from mcp.types import TextContent
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ─── Models ───────────────────────────────────────────────────────────────────


class FileWriteInput(BaseModel):
    """Validated input for the file write operation."""

    file_name: str = Field(
        ..., description="Name of the file to write to, i.e. file.txt"
    )
    content: str = Field(..., description="Content to write to the file")
    encoding: str = Field(default="utf-8", description="File encoding")
    mode: str = Field(
        default="write",
        description="Write mode: 'write' (overwrite), 'append', or 'create' (fail if exists)",
    )
    create_dirs: bool = Field(
        default=False, description="Create parent directories if they don't exist"
    )
    max_file_size: int = Field(
        default=10 * 1024 * 1024,  # 10MB
        description="Maximum allowed file size in bytes",
    )
    chunk_size: int = Field(default=8192, description="Chunk size for streaming writes")


class FileWriteResult(BaseModel):
    """Result model for a file write operation."""

    path: str = Field(..., description="The path that was written to")
    bytes_written: int = Field(default=0, description="Total bytes written")
    chunks_written: int = Field(default=0, description="Number of chunks written")
    success: bool = Field(default=False, description="Whether the write succeeded")
    error: Optional[str] = Field(default=None, description="Error message if any")
    mode: str = Field(default="write", description="The write mode used")


def stream_write_chunks(
    file_path: str,
    content: str,
    encoding: str = "utf-8",
    mode: str = "write",
    create_dirs: bool = True,
    chunk_size: int = 8192,
    max_file_size: int = 10 * 1024 * 1024,
) -> Generator[FileWriteResult, Any, None]:
    """
    Write content to a file in chunks, yielding progress after each chunk.
    Uses atomic writes (write to temp file, then rename) for 'write' and 'create' modes.
    """
    target = Path(file_path)

    # ── Pre-flight checks ──
    try:
        # Check content size
        content_bytes = content.encode(encoding)
        content_size = len(content_bytes)

        if content_size > max_file_size:
            yield FileWriteResult(
                path=file_path,
                success=False,
                error=f"Content size ({content_size} bytes) exceeds max_file_size ({max_file_size} bytes)",
                mode=mode,
            )
            return

        # Handle 'create' mode — must not already exist
        if mode == "create" and target.exists():
            yield FileWriteResult(
                path=file_path,
                success=False,
                error=f"File already exists and mode is 'create': {file_path}",
                mode=mode,
            )
            return

        # For append mode, check that combined size won't exceed limit
        if mode == "append" and target.exists():
            existing_size = target.stat().st_size
            if existing_size + content_size > max_file_size:
                yield FileWriteResult(
                    path=file_path,
                    success=False,
                    error=(
                        f"Appending would exceed max_file_size: "
                        f"existing ({existing_size}) + new ({content_size}) > {max_file_size}"
                    ),
                    mode=mode,
                )
                return

        # Create parent directories if requested
        if create_dirs:
            target.parent.mkdir(parents=True, exist_ok=True)
        elif not target.parent.exists():
            yield FileWriteResult(
                path=file_path,
                success=False,
                error=f"Parent directory does not exist: {target.parent}",
                mode=mode,
            )
            return

    except PermissionError:
        yield FileWriteResult(
            path=file_path,
            success=False,
            error=f"Permission denied: {file_path}",
            mode=mode,
        )
        return
    except Exception as e:
        yield FileWriteResult(
            path=file_path,
            success=False,
            error=f"Pre-flight error: {str(e)}",
            mode=mode,
        )
        return

    # ── Perform the write ──
    total_bytes = 0
    chunks_written = 0

    try:
        if mode == "append":
            # Append directly — atomic write doesn't make sense here
            with open(file_path, "a", encoding=encoding) as f:
                offset = 0
                while offset < content_size:
                    chunk = content_bytes[offset : offset + chunk_size].decode(encoding)
                    f.write(chunk)
                    written = len(chunk.encode(encoding))
                    total_bytes += written
                    chunks_written += 1
                    offset += chunk_size

                    logger.debug(
                        "Appended chunk %d (%d bytes) to %s",
                        chunks_written,
                        written,
                        file_path,
                    )

        else:
            # Atomic write: write to a temp file in the same directory, then rename
            fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding=encoding) as f:
                    offset = 0
                    while offset < content_size:
                        chunk = content_bytes[offset : offset + chunk_size].decode(
                            encoding
                        )
                        f.write(chunk)
                        written = len(chunk.encode(encoding))
                        total_bytes += written
                        chunks_written += 1
                        offset += chunk_size

                        logger.debug(
                            "Wrote chunk %d (%d bytes) to temp file for %s",
                            chunks_written,
                            written,
                            file_path,
                        )

                # Atomic rename
                os.replace(tmp_path, file_path)

            except BaseException:
                # Clean up temp file on any failure
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

        logger.info(
            "Successfully wrote %d bytes in %d chunks to %s",
            total_bytes,
            chunks_written,
            file_path,
        )

        yield FileWriteResult(
            path=file_path,
            bytes_written=total_bytes,
            chunks_written=chunks_written,
            success=True,
            mode=mode,
        )

    except PermissionError:
        error_msg = f"Permission denied writing to: {file_path}"
        logger.error(error_msg)
        yield FileWriteResult(
            path=file_path,
            success=False,
            error=error_msg,
            mode=mode,
        )
    except OSError as e:
        error_msg = f"OS error writing to {file_path}: {str(e)}"
        logger.error(error_msg)
        yield FileWriteResult(
            path=file_path,
            success=False,
            error=error_msg,
            mode=mode,
        )
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg)
        yield FileWriteResult(
            path=file_path,
            success=False,
            error=error_msg,
            mode=mode,
        )


def write_file(invocation: dict) -> Generator[dict[str, Any], Any, None]:
    """Main file writer handler with improved error handling.

    Invocation dict should contain keys: path, content, encoding, mode, create_dirs, chunk_size, max_file_size
    """
    try:
        payload = FileWriteInput.model_validate(invocation)

        for result in stream_write_chunks(
            payload.file_name,
            payload.content,
            payload.encoding,
            payload.mode,
            payload.create_dirs,
            payload.chunk_size,
            payload.max_file_size,
        ):
            yield result.model_dump()

    except Exception as e:
        error_msg = f"Validation or handler error: {str(e)}"
        logger.error(error_msg)
        yield FileWriteResult(
            path=invocation.get("input", invocation).get("path", "unknown"),
            success=False,
            error=error_msg,
            mode="write",
        ).model_dump()
