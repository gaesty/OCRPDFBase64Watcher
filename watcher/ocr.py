import os
import logging
from pathlib import Path
from typing import Optional, Tuple

# ocrmypdf must be installed for OCR; we will fall back to original file when OCR cannot run
try:  # pragma: no cover - optional dependency
    import ocrmypdf  # type: ignore
    from ocrmypdf.exceptions import PriorOcrFoundError  # type: ignore
except Exception:  # keep import error handled at runtime
    ocrmypdf = None  # type: ignore
    PriorOcrFoundError = Exception  # type: ignore


def ocr_to_bytes(
    pdf_path: Path, ocr_jobs: Optional[int], output_type: str = "pdf"
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
        logging.warning(
            "ocrmypdf is not installed; emitting original PDF bytes as base64"
        )
        return original, True

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        tmp_path = Path(tmp.name)
        try:
            ocrmypdf.ocr(
                str(pdf_path),
                str(tmp_path),
                # Conservative defaults; tweak if needed
                skip_text=True,  # do not re-OCR pages that already have text
                optimize=3,  # keep fast and lossless
                jobs=(
                    ocr_jobs if (ocr_jobs and ocr_jobs > 0) else (os.cpu_count() or 1)
                ),
                image_dpi=150,
                progress_bar=False,
                output_type=(
                    output_type or "pdf"
                ),  # 'pdf' for regular PDF, 'pdfa' for PDF/A
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
