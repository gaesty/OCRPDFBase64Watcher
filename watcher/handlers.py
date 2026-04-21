import base64
import datetime
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Set

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from .ocr import ocr_to_bytes
from .orm_odoo import get_connection_params, send_pdf_to_odoo
from .utils import compress_pdf_with_ghostscript, is_within, wait_for_file_ready


class PdfToBase64Handler(FileSystemEventHandler):
    def __init__(
        self,
        input_dir: Path,
        output_dir: Path,
        archive_dir: Optional[Path],
        use_polling: bool,
        retries: int,
        executor: ThreadPoolExecutor,
        ocr_jobs: Optional[int],
        output_type: str,
        jbig2_mode: str,
        history_file: Path,
        processed_files: Set[str],
    ) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.archive_dir = archive_dir
        self.use_polling = use_polling
        self.retries = retries
        self.executor = executor
        self.ocr_jobs = ocr_jobs
        self.output_type = output_type
        self.jbig2_mode = jbig2_mode
        self.history_file = history_file
        self.processed_files = processed_files

        self._lock = threading.Lock()
        self._history_lock = threading.Lock()
        self._in_flight: Set[Path] = set()

    def _update_history(self, filename: str):
        """Ajoute le fichier à l'historique sur disque et en mémoire."""
        logging.info(f"DEBUG: Updating history for {filename} in {self.history_file}")
        with self._history_lock:
            if filename in self.processed_files:
                return
            try:
                with open(self.history_file, "a", encoding="utf-8") as f:
                    now = datetime.datetime.now()
                    f.write(f"{now.strftime('%Y-%m-%d %H:%M:%S')} : {filename}\n")
                    f.flush()  # Force l'écriture du buffer vers l'OS
                    os.fsync(f.fileno())  # Force l'écriture de l'OS vers le disque
                self.processed_files.add(filename)
                logging.info(
                    f"Historique mis à jour : {filename} ajouté à .processed_history"
                )
            except Exception as e:
                logging.error(f"Impossible de mettre à jour l'historique : {e}")

    def submit_path(self, path: Path) -> None:
        # Vérification rapide avant soumission (optimisation)
        if path.name in self.processed_files:
            return

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

        # Vérification de l'historique (double check dans le thread)
        if name in self.processed_files:
            logging.debug(f"Ignoré (déjà dans l'historique) : {name}")
            return

        # If output_dir is inside input_dir, ignore anything inside output_dir
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
                path, self.ocr_jobs, self.output_type, self.jbig2_mode
            )

            base_stem = path.stem.split("_")[0]

            # Always emit a PDF into output_dir: OCR result if available, otherwise original bytes
            pdf_out_path = self.output_dir / f"{base_stem}.pdf"
            tmp_pdf_out = pdf_out_path.with_suffix(pdf_out_path.suffix + ".tmp")
            try:
                tmp_pdf_out.write_bytes(pdf_bytes)
                os.replace(tmp_pdf_out, pdf_out_path)
                logging.info(
                    f"Wrote OCR PDF -> {pdf_out_path} (from {'original' if used_original else 'OCR output'})"
                )

                # Try to compress the written PDF with Ghostscript (best-effort).
                try:
                    compressed_tmp = pdf_out_path.with_suffix(
                        pdf_out_path.suffix + ".gs.tmp"
                    )
                    compressed_ok = compress_pdf_with_ghostscript(
                        # "prepress" par "ebook"
                        pdf_out_path,
                        compressed_tmp,
                        preset="printer",
                    )
                    if compressed_ok:
                        os.replace(compressed_tmp, pdf_out_path)
                        pdf_bytes = pdf_out_path.read_bytes()
                except Exception:
                    logging.exception("Ghostscript compression step failed")
            except Exception as e:
                logging.warning(f"Failed to write OCR PDF to output directory ({e})")

            b64 = base64.b64encode(pdf_bytes).decode("ascii")

            # Ensure output dir exists
            self.output_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.output_dir / f"{base_stem}.base64"
            # Write atomically via temp and replace
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp_path.write_text(b64, encoding="utf-8")
            os.replace(tmp_path, out_path)
            logging.info(
                f"Wrote base64 -> {out_path} (from {'original' if used_original else 'OCR output'})"
            )

            if self.archive_dir:
                try:
                    # Tente de récupérer l'année depuis le chemin d'accès
                    match = re.search(r"\b(19\d{2}|20\d{2})\b", str(path))
                    pdf_year = (
                        match.group(1) if match else str(datetime.datetime.now().year)
                    )

                    year_dir = self.archive_dir / str(pdf_year)
                    year_dir.mkdir(parents=True, exist_ok=True)
                    archive_path = year_dir / out_path.name
                    archive_path.write_text(b64, encoding="utf-8")
                    logging.info(f"Archived base64 -> {archive_path}")
                except Exception as e:
                    logging.error(
                        f"Failed to archive base64 to {self.archive_dir}: {e}"
                    )

            # Check if Odoo is configured
            rpc_url, dbname, user, password = get_connection_params()
            if not all([rpc_url, dbname, user, password]):
                logging.info(
                    f"Odoo not configured. Marking {name} as processed locally."
                )
                self._update_history(name)
                # We do NOT delete files in local mode so the user can retrieve them
                return

            # Send to Odoo
            try:
                success = send_pdf_to_odoo(pdf_out_path.name, b64)

                if success:
                    self._update_history(name)

                    try:
                        if pdf_out_path.exists():
                            pdf_out_path.unlink()
                            logging.info(f"Deleted generated OCR PDF: {pdf_out_path}")
                        if out_path.exists():
                            # out_path.unlink()
                            logging.info(f"Deleted generated Base64: {out_path}")
                    except Exception as cleanup_err:
                        logging.warning(
                            f"Failed to cleanup generated files: {cleanup_err}"
                        )
                else:
                    logging.warning(
                        f"Odoo send returned False (Template not found?). Keeping generated files for inspection: {pdf_out_path.name}"
                    )
                    # Mark as processed to avoid infinite loop
                    self._update_history(name)

            except Exception as e:
                logging.error(f"Error triggering Odoo send: {e}")
                # En cas d'erreur technique, on garde aussi les fichiers
        except Exception as e:
            logging.exception(f"Failed to process {path}: {e}")

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self.submit_path(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        dest_path_attr = getattr(event, "dest_path", None)
        src_path_attr = getattr(event, "src_path", None)

        if dest_path_attr is not None:
            dest = Path(str(dest_path_attr))
        elif src_path_attr is not None:
            dest = Path(str(src_path_attr))
        else:
            # No valid path to process
            return

        self.submit_path(dest)
