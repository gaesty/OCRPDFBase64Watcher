#!/bin/bash
# Se placer dans le bon répertoire
cd /home/localalign/OCRPDFBase64Watcher

# Lancement via l'environnement virtuel avec les drapeaux liés par '='
exec ./venv/bin/python -m watcher.cli \
  --input-dir=/mnt/m_pdf \
  --output-dir=/mnt/d/archive_pdf \
  --archive-dir=/mnt/q_base64 \
  --poll