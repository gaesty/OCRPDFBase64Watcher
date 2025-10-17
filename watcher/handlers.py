import os
import base64
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Set

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from .utils import is_within, wait_for_file_ready
from .ocr import ocr_to_bytes


class PdfToBase64Handler(FileSystemEventHandler):
    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        use_polling: bool,
        retries: int,
        executor: ThreadPoolExecutor,
        ocr_jobs: Optional[int],
        output_type: str,
    ) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.use_polling = use_polling
        self.retries = retries
        self.executor = executor
        self.ocr_jobs = ocr_jobs
        self.output_type = output_type
        self._lock = threading.Lock()
        self._in_flight: Set[Path] = set()

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
        if not wait_for_file_ready(
            path, use_polling=self.use_polling, retries=self.retries
        ):
            logging.warning(f"File did not become ready: {path}")
            return

        try:
            pdf_bytes, used_original = ocr_to_bytes(
                path, self.ocr_jobs, self.output_type
            )

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
