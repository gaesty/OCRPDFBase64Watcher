import logging
import shutil
import subprocess
import time
from pathlib import Path

# Optional: use pikepdf to probe readiness; fallback to size-stable check if unavailable
try:  # pragma: no cover - optional
    import pikepdf  # type: ignore

    HAVE_PIKEPDF = True
except Exception:  # pragma: no cover - optional
    HAVE_PIKEPDF = False


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def wait_for_file_ready(
    path: Path, use_polling: bool, retries: int = 30, sleep_s: float = 0.5
) -> bool:
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
                with pikepdf.open(str(path)):  # pyright: ignore[reportPossiblyUnboundVariable]
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


def compress_pdf_with_ghostscript(
    input_path: Path, output_path: Path, preset: str = "prepress"
) -> bool:
    """Try to compress a PDF using Ghostscript (gs).

    preset: one of 'screen', 'ebook', 'printer', 'prepress', 'default'.
    Returns True on success, False otherwise. This is best-effort — callers
    should fall back to the original PDF if compression fails.
    """
    gs_exe = shutil.which("gs") or shutil.which("ghostscript")
    if not gs_exe:
        logging.debug("Ghostscript not found in PATH; skipping PDF compression")
        return False

    presets = {
        "screen": "/screen",
        "ebook": "/ebook",
        "printer": "/printer",
        "prepress": "/prepress",
        "default": "/default",
    }
    pdfsetting = presets.get(preset, "/printer")

    cmd = [
        gs_exe,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=2.0",
        f"-dPDFSETTINGS={pdfsetting}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        # --- AJOUTS POUR A4 ---
        "-sPAPERSIZE=a4",
        "-dFIXEDMEDIA",
        "-dPDFFitPage",
        # --- COMPRESSION OPTIMISÉE POUR L'IMPRESSION ---
        "-dDownsampleColorImages=true",
        "-dColorImageResolution=150",
        "-dColorImageDownsampleType=/Bicubic",
        "-dDownsampleGrayImages=true",
        "-dGrayImageResolution=200",
        "-dGrayImageDownsampleType=/Bicubic",
        "-dDownsampleMonoImages=true",
        "-dMonoImageResolution=400",
        "-dMonoImageDownsampleType=/Bicubic",
        # ----------------------
        f"-sOutputFile={str(output_path)}",
        str(input_path),
    ]

    try:
        res = subprocess.run(cmd, check=False)
        if res.returncode == 0 and output_path.exists():
            logging.info(f"Compressed PDF with Ghostscript -> {output_path}")
            return True
        logging.warning(
            f"Ghostscript failed (rc={res.returncode}); leaving original PDF"
        )
        return False
    except Exception as e:
        logging.warning(f"Ghostscript compression error: {e}")
        return False
