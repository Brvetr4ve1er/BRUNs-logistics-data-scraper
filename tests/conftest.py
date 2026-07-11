"""Pytest configuration shared by the test suite.

Keeps the repo root on sys.path so `import core...` / `import modules...` work
regardless of the working directory pytest is invoked from (CI, web sessions,
IDE runners), without every test needing its own sys.path shim.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
