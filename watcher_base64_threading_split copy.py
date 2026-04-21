#!/usr/bin/env python3

"""Backward-compatible entrypoint.

This script used to contain the whole implementation. It now delegates to the
modular watcher package CLI while preserving the same interface and behavior.
"""

from watcher.cli import app


if __name__ == "__main__":
    app()
