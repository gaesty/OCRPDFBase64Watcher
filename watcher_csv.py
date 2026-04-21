#!/usr/bin/env python3

"""
Entry point for CSV-based processing.

This script uses the shared watcher logic but is intended to be used with the --csv-file option.

Usage example:
    python3 watcher_csv.py --input-dir /mnt/share --output-dir ./ocr_out --csv-file ./files_to_process.csv

The CSV file should contain at least these columns:
    - complete_name: The filename (e.g. "CPA123.pdf")
    - file_path: The full path (e.g. "\\server\\share\\folder\\CPA123.pdf")
    - 2025-12-31 12:27:02 : CPA14080400001.PDF
"""

from watcher.cli_csv import app

if __name__ == "__main__":
    app()
