"""
File discovery tools for MCP. These are controlled CLI commands that the agent
can use to discover and parse files in the file system. They are designed to be safe and limited in scope, allowing the agent to find relevant information without risking security or privacy breaches. The tools include:
- `list_files`: List files in a directory with optional filtering by extension and recursion.
- `read_file`: Read the contents of a file with size limits to prevent abuse.
- `get_file_metadata`: Retrieve metadata about a file, such as size, creation date, and modification date.

"""

import json
import logging
import subprocess

from pydantic import BaseModel

logger = logging.getLogger(__file__)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class CommandRequest(BaseModel):
    command: str
    timeout: int = 30  # Default timeout of 30 seconds


class CommandResponse(BaseModel):
    stdout: str
    stderr: str
    returncode: int


# Allowlist of permitted base commands. Only commands whose first token appears
# in this set will be executed — everything else is blocked by default.
# Adjust this list to match the exact capabilities your use case requires.
ALLOWED_COMMANDS = (
    # Filesystem navigation & inspection
    "ls",  # List directory contents
    "cat",  # Print file contents
    "head",  # Print first N lines of a file
    "tail",  # Print last N lines of a file
    "pwd",  # Print working directory
    "find",  # Search for files by name, type, size, etc.
    "du",  # Disk usage of files/directories
    "df",  # Free disk space on mounted filesystems
    "stat",  # Detailed metadata about a file (size, permissions, timestamps)
    "file",  # Detect file type from content (not just extension)
    "wc",  # Word/line/character count
    # Text processing
    "grep",  # Search file contents by pattern
    "awk",  # Column-oriented text processing
    "sed",  # Stream editor for substitutions and transforms
    "sort",  # Sort lines of text
    "uniq",  # Remove or count duplicate lines
    "cut",  # Extract columns from delimited text
    "tr",  # Translate or delete characters
    "diff",  # Compare two files line by line
    # System information (read-only)
    "echo",  # Print a string (useful for testing/debugging)
    "date",  # Current date and time
    "uptime",  # How long the system has been running
    "whoami",  # Current username
    "uname",  # OS/kernel information
    "ps",  # Snapshot of running processes
    "env",  # Print environment variables
    "which",  # Locate a command on PATH
    "lsof",  # List open files and the processes using them
    # Git
    "git",  # Version control system (limited to safe subcommands like 'git status' 'git diff' or 'git log'
)


def check_command_safety(command: str) -> tuple[bool, str]:
    """
    Returns (is_safe, reason).
    Blocks the command if its base (first token) is not in ALLOWED_COMMANDS.
    """
    tokens = command.strip().split()
    if not tokens:
        return False, "Empty command."

    base = tokens[0]
    if base not in ALLOWED_COMMANDS:
        allowed = ", ".join(sorted(ALLOWED_COMMANDS))
        logger.warning(
            f"Blocked command: '{command}'. Reason: base '{base}' not in allowlist."
        )
        return False, (
            f"'{base}' is not in the list of allowed commands. "
            f"Permitted commands are: {allowed}"
        )

    return True, ""


def run_command(command: CommandRequest) -> CommandResponse:
    """
    Execute a shell command and return its output.

    Only commands whose base (first token) appear in the server's allowlist
    will be executed. Everything else is rejected before any subprocess is spawned.

    Args:
        command: The shell command to execute.
        timeout: Maximum time in seconds to wait for the command (default: 30).

    Returns:
        A dict with keys: stdout, stderr, returncode.
        If the command is blocked, returncode will be -1 and stderr will explain why.
    """
    is_safe, reason = check_command_safety(command.command)
    if not is_safe:
        return CommandResponse(stdout="", stderr=f"Blocked: {reason}", returncode=-1)

    logger.info("Executing command: '%s'", command.command)
    try:
        result = subprocess.run(
            command.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=command.timeout,
        )
        logger.info("Command executed. Return code: %d", result.returncode)
        response = CommandResponse(
            stdout=result.stdout, stderr=result.stderr, returncode=result.returncode
        )
        logger.info("Command output: %s", json.dumps(response.model_dump()), indent=2)
        return response
    except subprocess.TimeoutExpired:
        logger.error(
            "Command '%s' timed out after %d seconds.", command.command, command.timeout
        )
        return CommandResponse(
            stdout="",
            stderr=f"Command timed out after {command.timeout} seconds.",
            returncode=-1,
        )
    except Exception as e:
        logger.error("Error executing command '%s': %s", command.command, str(e))
        return CommandResponse(
            stdout="", stderr=f"Error executing command: {str(e)}", returncode=-1
        )
