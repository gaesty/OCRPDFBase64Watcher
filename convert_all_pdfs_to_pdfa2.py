#!/usr/bin/env python3
"""
    source OCRPDFBase64Watcher/.venv/bin/activate
    python3 OCRPDFBase64Watcher/convert_all_pdfs_to_pdfa2.py --input-dir /mnt/q_base64/XXXX --output-dir /mnt/d/archive_pdf/XXXX --preset printer --send-to-odoo --loglevel DEBUG
"""

import argparse
import base64
import logging
import os
import time
from pathlib import Path

from watcher.orm_odoo import send_pdf_to_odoo
from watcher.utils import convert_pdf_to_pdfa2, decode_base64_to_pdf, pdf_to_base64


def iter_base64_files(input_dir: Path, recursive: bool = True):
    if recursive:
        return sorted(p for p in input_dir.rglob("*.base64") if p.is_file())
    return sorted(p for p in input_dir.glob("*.base64") if p.is_file())


def resolve_output_path(input_dir: Path, output_dir: Path, base64_file: Path) -> Path:
    """Preserve year folders when input files are stored under a year directory.

    Examples:
      input_dir=archive_dir, file=archive_dir/2026/CPA123.base64 -> output_dir/2026/CPA123_pdfa2.base64
      input_dir=ocr_out, file=ocr_out/CPA123.base64 -> output_dir/CPA123_pdfa2.base64
    """
    relative = base64_file.relative_to(input_dir)
    if relative.parts and len(relative.parts) > 1:
        year_candidate = relative.parts[0]
        if year_candidate.isdigit() and len(year_candidate) == 4:
            return output_dir / year_candidate / relative.name

    return output_dir / relative.name


def process_base64_folder(
    input_dir: Path,
    output_dir: Path,
    preset: str = "printer",
    recursive: bool = True,
    send_to_odoo: bool = False,
) -> list[Path]:
    input_dir = input_dir.expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(
            f"Input directory not found or not a directory: {input_dir}"
        )

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    files = iter_base64_files(input_dir, recursive=recursive)
    logging.info("Found %d .base64 file(s) under %s", len(files), input_dir)
    if not files:
        logging.warning(
            "No .base64 files found in %s. Check the mount, the path, and the extension (.base64).",
            input_dir,
        )

    converted: list[Path] = []
    for index, b64_file in enumerate(files, start=1):
        started = time.time()
        pdf_name = b64_file.stem
        logging.info("[%d/%d] Processing %s", index, len(files), b64_file)

        output_base = resolve_output_path(input_dir, output_dir, b64_file)
        output_base.parent.mkdir(parents=True, exist_ok=True)

        pdf_path = output_base.with_suffix(".pdf")
        logging.debug("[%d/%d] Decode %s -> %s", index, len(files), b64_file, pdf_path)
        if not decode_base64_to_pdf(b64_file, pdf_path):
            logging.warning(
                "[%d/%d] Skipping invalid base64 file: %s", index, len(files), b64_file
            )
            continue

        pdfa2_path = output_base.with_name(f"{pdf_name}_pdfa2.pdf")
        logging.debug(
            "[%d/%d] Converting PDF/A-2: %s -> %s",
            index,
            len(files),
            pdf_path,
            pdfa2_path,
        )
        if not convert_pdf_to_pdfa2(pdf_path, pdfa2_path, preset=preset):
            logging.warning(
                "[%d/%d] Could not convert PDF/A-2 for %s", index, len(files), pdf_path
            )
            continue

        pdfa2_base64 = pdf_to_base64(pdfa2_path)
        base64_out = output_base.with_name(f"{pdf_name}_pdfa2.base64")
        base64_out.write_text(pdfa2_base64, encoding="utf-8")
        converted.append(base64_out)
        logging.info(
            "[%d/%d] Converted and wrote %s (%.1f s)",
            index,
            len(files),
            base64_out,
            time.time() - started,
        )

        if send_to_odoo:
            filename = f"{pdf_name}.pdf"
            try:
                logging.info("[%d/%d] Sending %s to Odoo", index, len(files), filename)
                if send_pdf_to_odoo(filename, pdfa2_base64):
                    logging.info(
                        "[%d/%d] Sent %s to Odoo in PDF/A-2 format",
                        index,
                        len(files),
                        filename,
                    )
                else:
                    logging.warning(
                        "[%d/%d] Odoo rejected %s; base64 kept locally",
                        index,
                        len(files),
                        filename,
                    )
            except Exception as exc:
                logging.warning(
                    "[%d/%d] Could not send %s to Odoo: %s",
                    index,
                    len(files),
                    filename,
                    exc,
                )

    return converted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decode .base64 PDFs, convert them to PDF/A-2, re-encode to base64, and optionally send to Odoo."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("./ocr_out"),
        help="Directory containing the .base64 files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./pdfa2"),
        help="Directory to receive the generated PDF/A-2 files and .base64 outputs",
    )
    parser.add_argument(
        "--preset",
        choices=["screen", "ebook", "printer", "prepress", "default"],
        default="printer",
        help="Ghostscript preset for PDF/A-2 conversion",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        default=True,
        help="Process .base64 files recursively under input-dir",
    )
    parser.add_argument(
        "--no-recursive",
        dest="recursive",
        action="store_false",
        help="Only process .base64 files in the top directory",
    )
    parser.add_argument(
        "--send-to-odoo",
        action="store_true",
        help="Send the resulting PDF/A-2 base64 to Odoo if ODOO_* variables are configured",
    )
    parser.add_argument(
        "--loglevel",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=os.getenv("LOGLEVEL", "INFO").upper(),
        help="Logging level to display in the terminal",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.loglevel.upper(), logging.INFO),
        format="%(levelname)s:%(message)s",
    )
    logging.debug("Debug logging enabled")
    logging.debug("Input dir: %s", args.input_dir)
    logging.debug("Output dir: %s", args.output_dir)
    logging.debug("Recursive: %s", args.recursive)
    logging.debug("Preset: %s", args.preset)
    logging.debug("Send to Odoo: %s", args.send_to_odoo)

    try:
        converted = process_base64_folder(
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            preset=args.preset,
            recursive=args.recursive,
            send_to_odoo=args.send_to_odoo,
        )
    except Exception as exc:
        logging.error("%s", exc)
        raise SystemExit(1)

    print(f"Converted {len(converted)} .base64 file(s) to PDF/A-2 .base64")
    for item in converted:
        print(item)


if __name__ == "__main__":
    main()
