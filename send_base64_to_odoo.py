import argparse
import csv
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Permet d'importer le module watcher situé dans le même dossier
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from watcher.orm_odoo import send_pdf_to_odoo

# Configuration des logs pour écrire dans un fichier ET dans la console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(threadName)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("send_base64_to_odoo.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

MAX_WORKERS = 2  # Nombre de requêtes simultanées
HISTORY_FILE = "processed_history.txt"  # Fichier listant les documents déjà traités

# Verrou pour sécuriser l'écriture dans le fichier d'historique par plusieurs threads
history_lock = threading.Lock()


def load_processed_history():
    """Charge la liste des fichiers déjà traités depuis le fichier d'historique."""
    if not os.path.exists(HISTORY_FILE):
        return set()
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def mark_as_processed(filename):
    """Ajoute de manière sécurisée (thread-safe) un fichier à l'historique."""
    with history_lock:
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(filename + "\n")


def load_priority_ids(csv_filepath):
    """Charge la liste des identifiants prioritaires depuis un fichier CSV."""
    priority_ids = set()
    if not os.path.exists(csv_filepath):
        logging.warning(
            f"Fichier CSV de priorité '{csv_filepath}' introuvable. Traitement standard appliqué."
        )
        return priority_ids

    try:
        with open(csv_filepath, mode="r", encoding="utf-8") as f:
            reader = csv.reader(f)
            # On ignore l'en-tête (ex: "Quality Check")
            next(reader, None)
            for row in reader:
                if row and row[0].strip():  # Sécurité contre les lignes vides
                    priority_ids.add(row[0].strip())
    except Exception as e:
        logging.error(f"Erreur lors de la lecture du CSV de priorité : {e}")

    return priority_ids


def get_priority(filename, priority_ids):
    """
    Définit la priorité d'un fichier en fonction des IDs du CSV.
    Retourne 0 si l'ID est trouvé dans le nom du fichier (priorité haute), sinon 1.
    """
    for priority_id in priority_ids:
        # On vérifie si l'identifiant (ex: C99021200007) est présent dans le nom du fichier
        if priority_id in filename:
            return 0
    return 1


def process_single_file(filename, input_dir, processed_set):
    """Fonction dédiée au traitement d'un seul fichier, facilitant l'exécution parallèle."""
    # Vérification anti-doublon
    if filename in processed_set:
        logging.info(f"Ignoré (déjà traité) : {filename}")
        return True

    filepath = os.path.join(input_dir, filename)

    # Lecture du contenu base64
    try:
        with open(filepath, "r") as f:
            b64_content = f.read().strip()
    except Exception as e:
        logging.error(f"Impossible de lire le fichier {filepath}: {e}")
        return False

    # On remplace l'extension par .pdf pour le nom du fichier envoyé à Odoo
    pdf_filename = filename.rsplit(".", 1)[0] + ".pdf"

    logging.info(f"Envoi de {pdf_filename} à Odoo...")
    try:
        # L'appel à send_pdf_to_odoo gère la recherche et la création/mise à jour
        success = send_pdf_to_odoo(pdf_filename, b64_content)

        if success:
            logging.info(f"Succès pour {filename}. Enregistrement dans l'historique.")
            mark_as_processed(filename)
        else:
            logging.warning(f"Échec ou absence de correspondance pour {filename}.")
        return success
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi de {filename} à Odoo: {e}")
        return False


def process_base64_files(input_dir, csv_file):
    if not os.path.isdir(input_dir):
        logging.error(f"Le dossier '{input_dir}' n'existe pas.")
        return

    files = [f for f in os.listdir(input_dir) if f.endswith(".base64")]
    if not files:
        logging.info(f"Aucun fichier .base64 trouvé dans '{input_dir}'.")
        return

    # Chargement de l'historique et des identifiants prioritaires
    processed_set = load_processed_history()
    priority_ids = load_priority_ids(csv_file)

    # Filtrer les fichiers qui ne sont pas encore dans l'historique
    files_to_process = [f for f in files if f not in processed_set]

    # --- TRI PAR PRIORITÉ ---
    files_to_process.sort(key=lambda f: get_priority(f, priority_ids))

    logging.info(f"{len(files)} fichier(s) au total dans '{input_dir}'.")
    logging.info(f"{len(files_to_process)} nouveau(x) fichier(s) à traiter.")

    if not files_to_process:
        return

    # Utilisation du ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                process_single_file, filename, input_dir, processed_set
            ): filename
            for filename in files_to_process
        }

        for future in as_completed(futures):
            filename = futures[future]
            try:
                future.result()
            except Exception as exc:
                logging.error(
                    f"Une exception critique inattendue s'est produite pour {filename} : {exc}"
                )


if __name__ == "__main__":
    # --- CONFIGURATION ARGPARSE ---
    parser = argparse.ArgumentParser(
        description="Envoi de fichiers base64 vers Odoo avec gestion de priorité via CSV."
    )

    # Arguments disponibles en ligne de commande
    parser.add_argument(
        "--dir",
        type=str,
        default="ocr_out",
        help="Dossier contenant les fichiers .base64 à traiter (défaut: ocr_out)",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default="priorites.csv",
        help="Chemin vers le fichier CSV contenant les ID prioritaires (défaut: priorites.csv)",
    )

    args = parser.parse_args()

    # Lancement de la logique principale avec les arguments
    process_base64_files(input_dir=args.dir, csv_file=args.csv)
