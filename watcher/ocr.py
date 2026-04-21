import logging
import os
import shutil
from pathlib import Path
from typing import Optional, Tuple

# ocrmypdf must be installed for OCR; we will fall back to original file when OCR cannot run
try:  # pragma: no cover - optional dependency
    import ocrmypdf  # type: ignore

    try:
        from ocrmypdf.exceptions import PriorOcrFoundError  # type: ignore
    except Exception:
        # Some distro builds may not expose this symbol; degrade gracefully
        class PriorOcrFoundError(Exception):  # type: ignore
            pass

except Exception as _ocr_import_err:  # keep import error handled at runtime
    ocrmypdf = None  # type: ignore

    class PriorOcrFoundError(Exception):  # type: ignore
        pass

    # Note: logging may not be configured yet; this is best-effort
    try:
        logging.warning("Failed to import ocrmypdf (%s); OCR disabled", _ocr_import_err)
    except Exception:
        pass


def ocr_to_bytes(
    pdf_path: Path,
    ocr_jobs: Optional[int],
    output_type: str = "pdf",
    jbig2_mode: str = "off",
) -> Tuple[bytes, bool]:
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
        logging.warning("ocrmypdf is unavailable; using original PDF bytes")
        return original, True

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        tmp_path = Path(tmp.name)
        try:
            kwargs = dict(
                skip_text=True,
                optimize=0,  #
                jobs=ocr_jobs if (ocr_jobs and ocr_jobs > 0) else (os.cpu_count() or 1),
                progress_bar=False,
                pdf_renderer="sandwich",
                output_type=(output_type or "pdf"),
                rotate_pages=True,
                fast_web_view=999999,
                deskew=True,
                # clean_final=True,
                # clean=True,
            )

            # If user requested JBIG2, ensure a jbig2 binary is available; try to extend PATH with bundled libexec if present
            if jbig2_mode in {"lossless", "lossy"}:
                jbig2_bin = shutil.which("jbig2") or shutil.which("jbig2enc")
                if not jbig2_bin:
                    # Try project-local libexec path
                    here = Path(__file__).resolve()
                    libexec = (
                        here.parents[2] / "pdfsizeopt" / "pdfsizeopt_libexec" / "jbig2"
                    )
                    if libexec.exists():
                        os.environ["PATH"] = (
                            f"{str(libexec)}:{os.environ.get('PATH', '')}"
                        )
                        jbig2_bin = shutil.which("jbig2") or shutil.which("jbig2enc")
                if not jbig2_bin:
                    logging.warning(
                        "JBIG2 requested (%s) but no 'jbig2' binary found in PATH; proceeding without JBIG2",
                        jbig2_mode,
                    )
                else:
                    logging.info(
                        "JBIG2 binary detected at %s; enabling %s mode",
                        jbig2_bin,
                        jbig2_mode,
                    )
                    # Add appropriate flag depending on mode; use try to tolerate older ocrmypdf
                    try:
                        if jbig2_mode == "lossy":
                            kwargs["jbig2_lossy"] = True
                        elif jbig2_mode == "lossless":
                            # Some ocrmypdf versions do not expose a distinct 'lossless' flag.
                            # We pass a no-op; lossless JBIG2 may not be used unless supported by the installed version.
                            kwargs["jbig2_lossy"] = False
                    except Exception:
                        # Ignore if API doesn't support these flags
                        logging.warning(
                            "Current ocrmypdf does not support JBIG2 flags; ignoring --jbig2=%s",
                            jbig2_mode,
                        )

            ocrmypdf.ocr(
                str(pdf_path),
                str(tmp_path),
                # kwargs include skip_text/optimize/jobs/progress_bar/output_type and optional jbig2 flags
                **kwargs,  # pyright: ignore[reportArgumentType]
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
