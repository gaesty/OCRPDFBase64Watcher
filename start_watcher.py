#!/usr/bin/env python3
import os
import sys
import logging
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from watchdog.observers.polling import PollingObserver

# 1. Chargement de l'environnement AVANT les imports locaux
from dotenv import load_dotenv
load_dotenv("/home/localalign/OCRPDFBase64Watcher/.env")

# Injection sécurisée
os.environ["OCR_INPUT_DIRECTORY"] = "/mnt/m_pdf"
os.environ["OCR_OUTPUT_DIRECTORY"] = "/mnt/d/archive_pdf"
os.environ["OCR_ARCHIVE_DIRECTORY"] = "/mnt/q_base64"

# 2. Imports locaux
from watcher.handlers import PdfToBase64Handler
from watcher.cli import load_history
from watcher.monitoring import send_kuma_push, verifier_espace_disque
from watcher.orm_odoo import get_uid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def supervision_kuma_en_arriere_plan():
    """
    Cette fonction tourne dans un thread séparé et ping Kuma
    toutes les 60 secondes, indépendamment du reste du script.
    """
    while True:
        try:
            verifier_espace_disque()
            uid = get_uid()
            if not uid:
                raise ValueError("Authentification Odoo impossible.")
            send_kuma_push("up", "OK - Disque, Odoo et Watchdog fonctionnels")
        except Exception as e:
            logging.error(f"Erreur de supervision: {e}")
            send_kuma_push("down", str(e))
            
        time.sleep(60)

def main():
    logging.info("Démarrage du Watcher en mode direct...")
    
    input_dir = Path(os.environ["OCR_INPUT_DIRECTORY"])
    output_dir = Path(os.environ["OCR_OUTPUT_DIRECTORY"])
    archive_dir = Path(os.environ["OCR_ARCHIVE_DIRECTORY"])
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    history_file = output_dir / ".processed_history"
    processed_files = load_history(history_file)
    logging.info(f"Historique chargé : {len(processed_files)} fichiers.")

    # 3. Lancement de la supervision Kuma en parallèle (Thread)
    send_kuma_push("up", "Démarrage du service OCR...")
    kuma_thread = threading.Thread(target=supervision_kuma_en_arriere_plan, daemon=True)
    kuma_thread.start()
    logging.info("Thread de supervision Uptime Kuma démarré en arrière-plan.")

    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ocr-worker")
    
    handler = PdfToBase64Handler(
        input_dir,
        output_dir,
        archive_dir,
        True,  # use_polling
        30,    # retries
        executor,
        1,     # ocr_jobs
        "pdf", # output_type
        "off", # jbig2_mode
        history_file,
        processed_files
    )

    logging.info("Exécution du scan initial sur le montage réseau...")
    for pdf in sorted(input_dir.rglob("*.pdf")):
        if output_dir in pdf.parents or pdf.name in processed_files:
            continue
        try:
            handler.submit_path(pdf)
        except Exception as e:
            logging.error(f"Erreur scan initial {pdf}: {e}")

    observer = PollingObserver()
    observer.schedule(handler, str(input_dir), recursive=True)
    observer.start()
    logging.info(f"Surveillance active (Polling) sur {input_dir}")

    # 4. Boucle principale qui maintient le script en vie
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Arrêt du watcher...")
    finally:
        observer.stop()
        observer.join()
        executor.shutdown(wait=True)

if __name__ == "__main__":
    main()