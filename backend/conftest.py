# conftest.py — pytest configuration for the backend test suite.
import pytest


# Extend collection to also pick up *_tests.py files (e.g. unit_tests.py).
# By default pytest only discovers test_*.py and *_test.py.
def pytest_collect_file(parent, file_path):
    if file_path.suffix == ".py" and file_path.name.endswith("_tests.py"):
        return pytest.Module.from_parent(parent, path=file_path)


# Exclude standalone scripts that use asyncio.run() directly from pytest
# collection.  These are run manually via `python <script>.py`.
collect_ignore = ["smoke_test.py"]


# Use auto asyncio mode so every async test function is run with pytest-asyncio
# without requiring per-test @pytest.mark.asyncio decoration.
def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as async")
