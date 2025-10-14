#!/usr/bin/env python3

import os
import time
import base64
import logging
import threading
from pathlib import Path
from typing import Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

import typer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

# Optional: use pikepdf to probe readiness; fallback to size-stable check if unavailable
try:
    import pikepdf  # type: ignore
    HAVE_PIKEPDF = True
except Exception:
    HAVE_PIKEPDF = False

# ocrmypdf must be installed for OCR; we will fall back to original file when OCR cannot run
try:
    import ocrmypdf  # type: ignore
    from ocrmypdf.exceptions import PriorOcrFoundError  # type: ignore
except Exception:  # keep import error handled at runtime
    ocrmypdf = None  # type: ignore
    PriorOcrFoundError = Exception  # type: ignore

app = typer.Typer(add_completion=False, no_args_is_help=True)


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def wait_for_file_ready(path: Path, use_polling: bool, retries: int = 30, sleep_s: float = 0.5) -> bool:
    """Wait until a file is fully written and ready to read.

    Ready when either:
      - pikepdf can open it (preferred), or
      - size is stable across two checks and it exists.
    """
    last_size = -1
    stable_count = 0

    for _ in range(max(1, retries)):
        if not path.exists():
            time.sleep(sleep_s)
            continue

        # Try opening with pikepdf if available
        if HAVE_PIKEPDF:
            try:
                with pikepdf.open(str(path)):
                    return True
            except Exception:
                # Not ready yet; fall back to size check
                pass

        # Size-stable check
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            size = -1
        if size > 0 and size == last_size:
            stable_count += 1
            if stable_count >= 2:  # two consecutive stable checks
                return True
        else:
            stable_count = 0

        last_size = size
        time.sleep(sleep_s)

    return path.exists()


def ocr_to_bytes(pdf_path: Path, ocr_jobs: Optional[int]) -> Tuple[bytes, bool]:
    """Return (pdf_bytes, used_original).

    - If OCR succeeds, returns OCR'd PDF bytes and used_original=False
    - If OCR is skipped due to PriorOcrFoundError or fails, returns original bytes and used_original=True
    """
    # Always ensure we can at least emit the original PDF
    try:
        original = pdf_path.read_bytes()
    except Exception as e:
        raise RuntimeError(f"Unable to read input file {pdf_path}: {e}")

    # If ocrmypdf isn't available, fall back to original
    if ocrmypdf is None:
        logging.warning("ocrmypdf is not installed; emitting original PDF bytes as base64")
        return original, True

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        tmp_path = Path(tmp.name)
        try:
            ocrmypdf.ocr(
                str(pdf_path),
                str(tmp_path),
                # Conservative defaults; tweak if needed
                skip_text=True,            # do not re-OCR pages that already have text
                optimize=0,                # keep fast and lossless
                jobs=(ocr_jobs if (ocr_jobs and ocr_jobs > 0) else (os.cpu_count() or 1)),
                image_dpi=150,
                progress_bar=False,
            )
            data = tmp_path.read_bytes()
            if data:
                return data, False
            # If somehow empty, fall back to original
            logging.warning("OCR output was empty; falling back to original bytes")
            return original, True
        except PriorOcrFoundError:
            logging.info("Prior OCR detected; using original PDF bytes for base64")
            return original, True
        except Exception as e:
            logging.warning(f"OCR failed ({e}); falling back to original PDF bytes")
            return original, True


class PdfToBase64Handler(FileSystemEventHandler):
    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        use_polling: bool,
        retries: int,
        executor: ThreadPoolExecutor,
        ocr_jobs: Optional[int],
    ) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.use_polling = use_polling
        self.retries = retries
        self.executor = executor
        self.ocr_jobs = ocr_jobs
        self._lock = threading.Lock()
        self._in_flight = set()

    def submit_path(self, path: Path) -> None:
        # Deduplicate submissions for the same path while it's in-flight
        with self._lock:
            if path in self._in_flight:
                logging.debug(f"Already processing, skipping duplicate event: {path}")
                return
            self._in_flight.add(path)

        def _task():
            try:
                self._handle_path(path)
            finally:
                with self._lock:
                    self._in_flight.discard(path)

        self.executor.submit(_task)

    def _handle_path(self, path: Path) -> None:
        # Ignore directories and non-pdf files
        if path.is_dir() or path.suffix.lower() != ".pdf":
            return

        # Ignore files produced by ourselves or common OCR outputs
        name = path.name
        if name.endswith("_ocr.pdf") or name.endswith(".ocr.pdf"):
            return

        # If output_dir is inside input_dir, ignore anything inside output_dir
        # Ne skip que si le dossier de sortie est différent du dossier d'entrée
        if self.output_dir != self.input_dir and is_within(path, self.output_dir):
            return

        # Ensure file is complete/ready
        if not wait_for_file_ready(path, use_polling=self.use_polling, retries=self.retries):
            logging.warning(f"File did not become ready: {path}")
            return

        try:
            pdf_bytes, used_original = ocr_to_bytes(path, self.ocr_jobs)

            # Always emit a PDF into output_dir: OCR result if available, otherwise original bytes
            pdf_out_path = self.output_dir / f"{path.stem}_ocr.pdf"
            tmp_pdf_out = pdf_out_path.with_suffix(pdf_out_path.suffix + ".tmp")
            try:
                tmp_pdf_out.write_bytes(pdf_bytes)
                os.replace(tmp_pdf_out, pdf_out_path)
                logging.info(
                    f"Wrote OCR PDF -> {pdf_out_path} (from {'original' if used_original else 'OCR output'})"
                )
            except Exception as e:
                logging.warning(f"Failed to write OCR PDF to output directory ({e})")

            b64 = base64.b64encode(pdf_bytes).decode("ascii")

            # Ensure output dir exists
            self.output_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.output_dir / f"{path.stem}.base64"
            # Write atomically via temp and replace
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp_path.write_text(b64, encoding="utf-8")
            os.replace(tmp_path, out_path)
            logging.info(
                f"Wrote base64 -> {out_path} (from {'original' if used_original else 'OCR output'})"
            )
        except Exception as e:
            logging.exception(f"Failed to process {path}: {e}")

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self.submit_path(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # Prefer destination path on moved events
        try:
            dest = Path(getattr(event, "dest_path", event.src_path))
        except Exception:
            dest = Path(event.src_path)
        self.submit_path(dest)


@app.command()
def main(
    input_dir: Path = typer.Option(
        ..., "--input-dir", exists=True, file_okay=False, dir_okay=True, readable=True,
        help="Folder to watch for incoming PDFs", envvar="OCR_INPUT_DIRECTORY"
    ),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir",
        help="Where to write OCR PDFs and .base64 files; defaults to <input-dir>/base64",
        envvar="OCR_OUTPUT_DIRECTORY"
    ),
    workers: Optional[int] = typer.Option(
        None, "--workers",
        help="Max number of PDFs to process concurrently",
        envvar="OCR_WORKERS",
    ),
    ocr_jobs: Optional[int] = typer.Option(
        None, "--ocr-jobs",
        help="Parallel jobs per file for ocrmypdf (set 1 when using multiple workers to avoid oversubscription)",
        envvar="OCR_JOBS",
    ),
    initial_scan: bool = typer.Option(
        True, "--initial-scan/--no-initial-scan",
        help="Process existing PDFs at startup", envvar="OCR_INITIAL_SCAN"
    ),
    retries: int = typer.Option(30, "--retries", help="Max readiness checks before giving up"),
    use_polling: Optional[bool] = typer.Option(None, "--poll/--no-poll", help="Force polling observer (auto if under /mnt)"),
    loglevel: str = typer.Option("INFO", "--loglevel", help="Logging level: DEBUG, INFO, WARNING, ERROR"),
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

    logging.info(f"Concurrency -> workers: {workers}, ocr_jobs per file: {ocr_jobs if ocr_jobs is not None else (os.cpu_count() or 1)}")

    executor = ThreadPoolExecutor(max_workers=workers or 1, thread_name_prefix="ocr-worker")
    handler = PdfToBase64Handler(input_dir, output_dir, use_polling, retries, executor, ocr_jobs)

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

    observer = (PollingObserver() if use_polling else Observer())
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
