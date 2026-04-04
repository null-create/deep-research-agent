# conftest.py — pytest configuration for the backend test suite.

# Exclude standalone scripts that use asyncio.run() directly from pytest
# collection.  These are run manually via `python <script>.py`.
collect_ignore = ["smoke_test.py"]

