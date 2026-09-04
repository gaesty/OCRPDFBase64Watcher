#!/usr/bin/env python3
import sys
import os
from watcher.cli import app

# 1. Injection directe et absolue des variables d'environnement.
# Cela écrase complètement les potentiels problèmes de .env ou de systemd.
os.environ["OCR_INPUT_DIRECTORY"] = "/mnt/m_pdf"
os.environ["OCR_OUTPUT_DIRECTORY"] = "/mnt/d/archive_pdf"
os.environ["OCR_ARCHIVE_DIRECTORY"] = "/mnt/q_base64"

if __name__ == "__main__":
    # 2. Nettoyage total des arguments. 
    # On supprime tout ce que systemd pourrait tenter de transmettre par erreur.
    sys.argv = ["start.py"]
    
    # 3. Lancement de l'application. Typer va lire les variables d'environnement.
    app()