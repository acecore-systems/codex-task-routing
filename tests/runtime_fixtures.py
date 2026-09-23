"""Synthetic fixtures with inherited ACLs under the checkout, never the user home."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import uuid


@contextmanager
def runtime_directory():
    # Python 3.13 TemporaryDirectory's 0700 ACL excludes the restricted Windows
    # runner. Match the existing fixtures' mkdir behavior without changing ACLs.
    root = Path(__file__).resolve().parent / '.tmp-runtime-tests'
    root.mkdir(exist_ok=True)
    directory = root / f'fixture-{uuid.uuid4().hex}'
    directory.mkdir()
    try:
        yield directory
    finally:
        shutil.rmtree(directory)
