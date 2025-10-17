"""Watcher package: utilities to watch a folder, OCR PDFs, and emit base64 files.

Exports:
- app, main: Typer CLI entrypoints (from watcher.cli)
- PdfToBase64Handler: watchdog event handler (from watcher.handlers)
- ocr_to_bytes: OCR helper (from watcher.ocr)
- is_within, wait_for_file_ready: filesystem utilities (from watcher.utils)
"""

from .cli import app, main  # noqa: F401
from .handlers import PdfToBase64Handler  # noqa: F401
from .ocr import ocr_to_bytes  # noqa: F401
from .utils import is_within, wait_for_file_ready  # noqa: F401

__all__ = [
    "app",
    "main",
    "PdfToBase64Handler",
    "ocr_to_bytes",
    "is_within",
    "wait_for_file_ready",
]
