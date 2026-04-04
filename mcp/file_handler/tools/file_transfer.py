"""
File for handling file transfers, including uploading and downloading files.
This module provides tools for the MCP server to manage file transfers, including support for local file storage
within the MCP server's output directory. It includes functionality for listing available files, reading file contents, and streaming large files in chunks.
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)


def download_file(file_url: str, output_dir: str, headers: dict = None) -> str:
    """Download a file from a given URL and save it to the output directory."""
    try:
        response = requests.get(file_url, headers=headers or {}, stream=True)
        response.raise_for_status()  # Check if the request was successful

        # Extract filename from URL and create full file path
        filename = file_url.split("/")[-1]
        file_path = os.path.join(output_dir, filename)

        # Streaming download to handle large files
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"Downloaded file {filename} from {file_url} to {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"Error downloading file from {file_url}: {e}")
        raise


def upload_file(upload_url: str, file_path: str) -> None:
    """Upload a file from the output directory to a specified destination."""
    if not os.path.exists(file_path):
        logger.error("File not found for upload: {file_path}")
        raise FileNotFoundError(f"File not found: {file_path}")

    try:
        logger.info(f"Uploading file {os.path.basename(file_path)} to {upload_url}...")
        with open(file_path, "rb") as f:
            response = requests.post(
                upload_url, files={"file": (os.path.basename(file_path), f)}
            )
            response.raise_for_status()  # Check if the upload was successful
            if response.status_code in (200, 201):
                logger.info(f"Successfully uploaded file {file_path} to {upload_url}")
            else:
                logger.error(
                    f"Failed to upload file {file_path} to {upload_url}: {response.status_code} {response.text}"
                )
                raise Exception(
                    f"Upload failed with status code {response.status_code}"
                )
    except requests.RequestException as e:
        logger.error(f"HTTP error during file upload: {e}")
        raise
    except Exception as e:
        logger.error(f"Error uploading file {file_path} to {upload_url}: {e}")
        raise
