import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import typer
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from .handlers import PdfToBase64Handler
from .utils import is_within


app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def main(
    input_dir: Path = typer.Option(
        ...,
        "--input-dir",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Folder to watch for incoming PDFs",
        envvar="OCR_INPUT_DIRECTORY",
    ),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir",
        help="Where to write OCR PDFs and .base64 files; defaults to <input-dir>/base64",
        envvar="OCR_OUTPUT_DIRECTORY",
    ),
    workers: Optional[int] = typer.Option(
        None,
        "--workers",
        help="Max number of PDFs to process concurrently",
        envvar="OCR_WORKERS",
    ),
    ocr_jobs: Optional[int] = typer.Option(
        None,
        "--ocr-jobs",
        help="Parallel jobs per file for ocrmypdf (set 1 when using multiple workers to avoid oversubscription)",
        envvar="OCR_JOBS",
    ),
    initial_scan: bool = typer.Option(
        True,
        "--initial-scan/--no-initial-scan",
        help="Process existing PDFs at startup",
        envvar="OCR_INITIAL_SCAN",
    ),
    retries: int = typer.Option(
        30, "--retries", help="Max readiness checks before giving up"
    ),
    use_polling: Optional[bool] = typer.Option(
        None, "--poll/--no-poll", help="Force polling observer (auto if under /mnt)"
    ),
    loglevel: str = typer.Option(
        "INFO", "--loglevel", help="Logging level: DEBUG, INFO, WARNING, ERROR"
    ),
    output_type: str = typer.Option(
        "pdf",
        "--output-type",
        help="OCR output type: 'pdf' for regular PDF (default) or 'pdfa' for PDF/A-2B",
        envvar="OCR_OUTPUT_TYPE",
    ),
):
    """Watch a folder and write a <name>.base64 file for each PDF.

    - OCR is attempted; an OCR-processed PDF is written to the output directory when possible (fallback to original bytes).
    - A .base64 file is also written alongside the OCR PDF, encoding the same bytes.
    - By default, outputs go to <input>/base64 and existing PDFs are processed at startup.
    """
    # Logging
    logging.basicConfig(
        level=getattr(logging, loglevel.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Resolve defaults
    input_dir = input_dir.expanduser().resolve()
    if output_dir is None:
        output_dir = (input_dir / "base64").resolve()
    else:
        output_dir = output_dir.expanduser().resolve()

    # Auto-poll under /mnt to avoid inotify issues
    if use_polling is None:
        use_polling = str(input_dir).startswith("/mnt/")

    # Make sure output exists
    output_dir.mkdir(parents=True, exist_ok=True)

    logging.info(f"Watching: {input_dir}")
    logging.info(f"Output .base64 to: {output_dir}")
    logging.info(f"Observer: {'Polling' if use_polling else 'Inotify'}")
    logging.info(f"OCR PDFs and base64 output dir: {output_dir}")

    # Concurrency defaults: favor across-file concurrency; if workers>1 and ocr_jobs not set, set ocr_jobs=1
    if workers is None:
        cpu = os.cpu_count() or 2
        workers = max(1, cpu // 2)
    if (workers or 1) > 1 and (ocr_jobs is None):
        ocr_jobs = 1

    # Normalize and validate output_type
    output_type = (output_type or "pdf").strip().lower()
    if output_type not in {"pdf", "pdfa"}:
        logging.warning(f"Unknown --output-type '{output_type}', defaulting to 'pdf'")
        output_type = "pdf"

    logging.info(
        f"Concurrency -> workers: {workers}, ocr_jobs per file: {ocr_jobs if ocr_jobs is not None else (os.cpu_count() or 1)}; output_type: {output_type}"
    )

    executor = ThreadPoolExecutor(
        max_workers=workers or 1, thread_name_prefix="ocr-worker"
    )
    handler = PdfToBase64Handler(
        input_dir, output_dir, use_polling, retries, executor, ocr_jobs, output_type
    )

    # Initial scan for existing PDFs
    if initial_scan:
        for pdf in sorted(input_dir.rglob("*.pdf")):
            # skip inside output_dir to avoid loops
            if is_within(pdf, output_dir):
                continue
            try:
                handler.submit_path(pdf)
            except Exception as e:
                logging.exception(f"Initial scan failed for {pdf}: {e}")

    observer = PollingObserver() if use_polling else Observer()
    observer.schedule(handler, str(input_dir), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        logging.info("Stopping watcher...")
    finally:
        observer.stop()
        observer.join()
        executor.shutdown(wait=True)


if __name__ == "__main__":
    app()
