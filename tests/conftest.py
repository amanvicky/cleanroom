"""Shared test setup: every run starts with a clean cleanroom temp namespace,
so residue assertions measure what THIS test did, not what a previous crashed
run left behind."""

import os
import shutil
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _clean_sandbox_leftovers():
    tmp = tempfile.gettempdir()
    for name in os.listdir(tmp):
        if name.startswith("cleanroom-"):
            shutil.rmtree(os.path.join(tmp, name), ignore_errors=True)
    yield
