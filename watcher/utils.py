import base64
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


def decode_base64_to_pdf(base64_path: Path, output_pdf_path: Path) -> bool:
    """Decode a .base64 file to a PDF. Returns True on success."""
    try:
        raw = base64_path.read_text(encoding="utf-8").strip()
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1]
        pdf_bytes = base64.b64decode(raw, validate=True)
        output_pdf_path.parent.mkdir(parents=True, exist_ok=True)
        output_pdf_path.write_bytes(pdf_bytes)
        return True
    except Exception as exc:
        logging.error(
            "Failed to decode base64 %s -> %s: %s", base64_path, output_pdf_path, exc
        )
        return False


def pdf_to_base64(pdf_path: Path) -> str:
    """Encode a PDF file to base64 string."""
    return base64.b64encode(pdf_path.read_bytes()).decode("ascii")


def convert_pdf_to_pdfa2(
    input_path: Path,
    output_path: Path,
    preset: str = "printer",
) -> bool:
    """Convert a PDF to PDF/A-2 with Ghostscript."""
    gs_exe = shutil.which("gs") or shutil.which("ghostscript")
    if not gs_exe:
        logging.debug("Ghostscript not found in PATH; skipping PDF/A-2 conversion")
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
        "-dPDFA=2",
        "-dPDFACompatibilityPolicy=1",
        "-sProcessColorModel=DeviceRGB",
        f"-dPDFSETTINGS={pdfsetting}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        "-sPAPERSIZE=a4",
        "-dFIXEDMEDIA",
        "-dPDFFitPage",
        "-dDownsampleColorImages=true",
        "-dColorImageResolution=150",
        "-dColorImageDownsampleType=/Bicubic",
        "-dDownsampleGrayImages=true",
        "-dGrayImageResolution=200",
        "-dGrayImageDownsampleType=/Bicubic",
        "-dDownsampleMonoImages=true",
        "-dMonoImageResolution=400",
        "-dMonoImageDownsampleType=/Bicubic",
        f"-sOutputFile={output_path}",
        str(input_path),
    ]

    try:
        res = subprocess.run(cmd, check=False)
        if res.returncode == 0 and output_path.exists():
            logging.info("Converted to PDF/A-2 -> %s", output_path)
            return True
        logging.warning("Ghostscript failed (rc=%s) for %s", res.returncode, input_path)
        return False
    except Exception as exc:
        logging.warning(
            "Ghostscript PDF/A-2 conversion error for %s: %s", input_path, exc
        )
        return False


def convert_directory_pdfs_to_pdfa2(
    input_dir: Path,
    output_dir: Path | None = None,
    recursive: bool = True,
    preset: str = "printer",
) -> list[Path]:
    """Convert all PDFs in a directory to PDF/A-2. Writes to output_dir."""
    source_dir = Path(input_dir).expanduser().resolve()
    if not source_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {source_dir}")

    out_dir = Path(output_dir).expanduser().resolve() if output_dir else source_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_files = sorted(
        p
        for p in (source_dir.rglob("*.pdf") if recursive else source_dir.glob("*.pdf"))
        if p.is_file()
    )
    converted: list[Path] = []
    for pdf_file in pdf_files:
        target = out_dir / pdf_file.name
        if target.resolve() == pdf_file.resolve():
            target = out_dir / f"{pdf_file.stem}_pdfa2{pdf_file.suffix}"

        index = 1
        while target.exists() and target.resolve() != pdf_file.resolve():
            candidate = out_dir / f"{pdf_file.stem}_pdfa2_{index}{pdf_file.suffix}"
            if candidate.resolve() != pdf_file.resolve():
                target = candidate
                break
            index += 1

        if convert_pdf_to_pdfa2(pdf_file, target, preset=preset):
            converted.append(target)
        else:
            logging.warning("Skipping PDF/A-2 conversion for %s", pdf_file)

    logging.info("Converted %d PDF file(s) to PDF/A-2", len(converted))
    return converted


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
                with pikepdf.open(
                    str(path)
                ):  # pyright: ignore[reportPossiblyUnboundVariable]
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
        "-dPDFA=2",
        "-dPDFACompatibilityPolicy=1",
        "-sProcessColorModel=DeviceRGB",
        f"-dPDFSETTINGS={pdfsetting}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        "-sPAPERSIZE=a4",
        "-dFIXEDMEDIA",
        "-dPDFFitPage",
        "-dDownsampleColorImages=true",
        "-dColorImageResolution=150",
        "-dColorImageDownsampleType=/Bicubic",
        "-dDownsampleGrayImages=true",
        "-dGrayImageResolution=200",
        "-dGrayImageDownsampleType=/Bicubic",
        "-dDownsampleMonoImages=true",
        "-dMonoImageResolution=400",
        "-dMonoImageDownsampleType=/Bicubic",
        f"-sOutputFile={str(output_path)}",
        str(input_path),
    ]

    try:
        res = subprocess.run(cmd, check=False)
        if res.returncode == 0 and output_path.exists():
            logging.info(f"Compressed and converted to PDF/A-2 -> {output_path}")
            return True
        logging.warning(
            f"Ghostscript failed (rc={res.returncode}); leaving original PDF"
        )
        return False
    except Exception as e:
        logging.warning(f"Ghostscript compression error: {e}")
        return False
