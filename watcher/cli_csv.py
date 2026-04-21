import csv
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Set

import typer
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from .handlers import PdfToBase64Handler
from .utils import is_within

app = typer.Typer(add_completion=False, no_args_is_help=True)


def load_history(history_file: Path) -> Set[str]:
    """Charge la liste des fichiers déjà traités."""
    if not history_file.exists():
        return set()
    try:
        processed = set()
        for line in history_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            # Gestion du format avec horodatage "YYYY-MM-DD HH:MM:SS : filename"
            if " : " in line:
                # On découpe au premier séparateur pour garder le nom de fichier (même s'il contient des espaces)
                parts = line.split(" : ", 1)
                if len(parts) == 2:
                    processed.add(parts[1].strip())
                else:
                    processed.add(line)
            else:
                # Ancien format (juste le nom de fichier)
                processed.add(line)
        return processed
    except Exception as e:
        logging.warning(f"Impossible de lire l'historique {history_file}: {e}")
        return set()


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
    archive_dir: Optional[Path] = typer.Option(
        None,
        "--archive-dir",
        help="Where to archive generated .base64 files by year",
        envvar="OCR_ARCHIVE_DIRECTORY",
    ),
    csv_file: Optional[Path] = typer.Option(
        None,
        "--csv-file",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="CSV file containing list of files to process (replaces initial scan). Expects columns: complete_name, file_path",
    ),
    csv_only: bool = typer.Option(
        False,
        "--csv-only",
        help="Exit after processing CSV file (requires --csv-file)",
    ),
    workers: Optional[int] = typer.Option(
        None,
        "--workers",
        help="Max number of PDFs to process concurrently",
        envvar="OCR_WORKERS",
    ),
    workers_auto: Optional[str] = typer.Option(
        None,
        "--workers-auto",
        flag_value="half",
        help=(
            "When --workers is not provided: 'half' uses ~50% of CPUs (default), 'full' "
            "uses all CPUs (useful for free-threaded Python builds). If the option "
            "is given without a value it will default to 'half'."
        ),
        envvar="OCR_WORKERS_AUTO",
    ),
    ocr_jobs: Optional[int] = typer.Option(
        None,
        "--ocr-jobs",
        help="Parallel jobs per file for ocrmypdf (set 1 when using multiple workers to avoid oversubscription)",
        envvar="OCR_JOBS",
    ),
    initial_scan_flag: bool = typer.Option(
        False,
        "--initial-scan",
        help="Process existing PDFs at startup",
    ),
    no_initial_scan: bool = typer.Option(
        False,
        "--no-initial-scan",
        help="Skip processing existing PDFs at startup",
    ),
    retries: int = typer.Option(
        30, "--retries", help="Max readiness checks before giving up"
    ),
    poll: bool = typer.Option(
        False, "--poll", help="Force polling observer (overrides auto-detection)"
    ),
    no_poll: bool = typer.Option(
        False, "--no-poll", help="Force inotify observer (overrides auto-detection)"
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
    jbig2: str = typer.Option(
        "off",
        "--jbig2",
        help="JBIG2 compression for bitonal images: 'off' (default), 'lossless', or 'lossy' (requires jbig2 binary)",
        envvar="OCR_JBIG2",
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

    if archive_dir is not None:
        archive_dir = archive_dir.expanduser().resolve()

    # Resolve observer mode
    if poll and no_poll:
        raise typer.BadParameter("--poll and --no-poll are mutually exclusive")
    if poll:
        use_polling = True
    elif no_poll:
        use_polling = False
    else:
        # Auto-poll under /mnt to avoid inotify issues
        use_polling = str(input_dir).startswith("/mnt/")

    # Make sure output exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- HISTORIQUE ---
    history_file = output_dir / ".processed_history"
    processed_files = load_history(history_file)
    logging.info(f"Historique chargé : {len(processed_files)} fichiers déjà traités.")
    # ------------------

    logging.info(f"Watching: {input_dir}")
    logging.info(f"Output .base64 to: {output_dir}")
    logging.info(f"Observer: {'Polling' if use_polling else 'Inotify'}")
    logging.info(f"OCR PDFs and base64 output dir: {output_dir}")

    # Concurrency defaults: favor across-file concurrency; if workers>1 and ocr_jobs not set, set ocr_jobs=1
    if workers is None:
        cpu = os.cpu_count() or 2
        auto_mode = (workers_auto or "half").strip().lower()
        if auto_mode not in {"half", "full"}:
            logging.warning(
                f"Unknown --workers-auto '{workers_auto}', defaulting to 'half'"
            )
            auto_mode = "half"
        workers = cpu if auto_mode == "full" else max(1, cpu // 2)
    if (workers or 1) > 1 and (ocr_jobs is None):
        ocr_jobs = 1

    # Normalize and validate output_type
    output_type = (output_type or "pdf").strip().lower()
    if output_type not in {"pdf", "pdfa"}:
        logging.warning(f"Unknown --output-type '{output_type}', defaulting to 'pdf'")
        output_type = "pdf"

    # Normalize JBIG2 mode
    jbig2_mode = (jbig2 or "off").strip().lower()
    if jbig2_mode not in {"off", "lossless", "lossy"}:
        logging.warning(f"Unknown --jbig2 '{jbig2}', defaulting to 'off'")
        jbig2_mode = "off"

    # Try to detect free-threaded builds (if API present)
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    gil_status = None
    if callable(is_gil_enabled):
        try:
            gil_status = bool(is_gil_enabled())
        except Exception:
            gil_status = None

    logging.info(
        f"Concurrency -> workers: {workers}, ocr_jobs per file: {ocr_jobs if ocr_jobs is not None else (os.cpu_count() or 1)}; output_type: {output_type}; jbig2: {jbig2_mode}; GIL enabled: {gil_status if gil_status is not None else 'unknown'}"
    )

    executor = ThreadPoolExecutor(
        max_workers=workers or 1, thread_name_prefix="ocr-worker"
    )
    handler = PdfToBase64Handler(
        input_dir,
        output_dir,
        archive_dir,
        use_polling,
        retries,
        executor,
        ocr_jobs,
        output_type,
        jbig2_mode,
        history_file,  # Passé au handler
        processed_files,  # Passé au handler
    )

    # Determine initial scan behavior (defaults to True)
    if initial_scan_flag and no_initial_scan:
        raise typer.BadParameter(
            "--initial-scan and --no-initial-scan are mutually exclusive"
        )
    initial_scan = True
    if initial_scan_flag:
        initial_scan = True
    elif no_initial_scan:
        initial_scan = False
    else:
        # Allow environment override via OCR_INITIAL_SCAN
        env_is = os.getenv("OCR_INITIAL_SCAN")
        if env_is is not None:
            val = env_is.strip().lower()
            if val in {"1", "true", "yes", "y", "on"}:
                initial_scan = True
            elif val in {"0", "false", "no", "n", "off"}:
                initial_scan = False
            else:
                logging.warning(
                    f"Unrecognized OCR_INITIAL_SCAN='{env_is}', defaulting to initial scan enabled"
                )

    # Initial scan or CSV processing
    if csv_file:
        logging.info(f"Processing files from CSV: {csv_file}")
        try:
            with open(csv_file, "r", encoding="utf-8-sig") as f:
                # Attempt to detect delimiter, defaulting to tab if sniffing fails
                sample = ""
                try:
                    sample = f.read(2048)
                    f.seek(0)
                    dialect = csv.Sniffer().sniff(sample)
                except csv.Error:
                    f.seek(0)
                    dialect = None

                # Fallback to tab if sniffing failed or detected comma but we suspect tab
                delimiter = dialect.delimiter if dialect else "\t"
                # If the sample contains tabs and no commas, prefer tab
                if "\t" in sample and "," not in sample:
                    delimiter = "\t"

                reader = csv.DictReader(f, delimiter=delimiter)

                count = 0
                for row in reader:
                    # Clean keys
                    row = {k.strip(): v for k, v in row.items() if k}

                    candidate = None

                    # --- NEW LOGIC: complete_name + file_path (stripping prefix) ---
                    fname = row.get("complete_name")
                    raw_fp = row.get("file_path")

                    if not fname and not raw_fp:
                        logging.warning(
                            f"Skipping row, missing 'complete_name' or 'file_path': {row}"
                        )
                        continue

                    # 1. Try file_path as absolute path (if provided)
                    if raw_fp:
                        norm_fp = raw_fp.replace("\\", "/")
                        p = Path(norm_fp)
                        if p.is_absolute() and p.exists() and p.is_file():
                            candidate = p

                    # 2. Try stripping prefixes from file_path to match inside input_dir
                    if not candidate and raw_fp:
                        norm_fp = raw_fp.replace("\\", "/")
                        # Split path into parts. We use string split to handle Windows paths on Linux safely
                        # remove empty strings from split result
                        parts = [p for p in norm_fp.split("/") if p]

                        # Try to find the file by progressively removing leading directories
                        # e.g. 10.0.2.5/Quality/Certificates/2024/file.pdf
                        # -> input_dir/Quality/Certificates/2024/file.pdf
                        # -> input_dir/Certificates/2024/file.pdf
                        # -> input_dir/2024/file.pdf (MATCH)
                        for i in range(len(parts)):
                            sub_path = Path(*parts[i:])
                            p_check = input_dir / sub_path
                            if p_check.exists() and p_check.is_file():
                                candidate = p_check
                                logging.debug(
                                    f"CSV: Found by stripping prefix from file_path: {candidate}"
                                )
                                break

                    # 3. Fallback: Check complete_name at root of input_dir
                    if not candidate and fname:
                        p_root = input_dir / fname
                        if p_root.exists():
                            candidate = p_root

                    if not candidate:
                        logging.warning(
                            f"File from CSV not found: {row.get('complete_name') or row.get('file_path')}"
                        )
                        continue

                    # --- CHECK HISTORIQUE ---
                    if candidate.name in processed_files:
                        logging.debug(f"Skipping {candidate.name} (already in history)")
                        continue
                    # ------------------------

                    try:
                        handler.submit_path(candidate)
                        count += 1
                    except Exception as e:
                        logging.exception(f"CSV processing failed for {candidate}: {e}")

                logging.info(f"Submitted {count} files from CSV for processing.")

            if csv_only:
                logging.info("CSV processing complete. Waiting for jobs to finish...")
                executor.shutdown(wait=True)
                logging.info("Done.")
                return

        except Exception as e:
            logging.error(f"Failed to process CSV file {csv_file}: {e}")
            if csv_only:
                executor.shutdown(wait=True)
                return

    elif initial_scan:
        for pdf in sorted(input_dir.rglob("*.pdf")):
            # skip inside output_dir to avoid loops
            if is_within(pdf, output_dir):
                continue

            # --- CHECK HISTORIQUE ---
            if pdf.name in processed_files:
                logging.debug(f"Skipping {pdf.name} (already in history)")
                continue
            # ------------------------

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
