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

OCR_OUT_DIR = "ocr_out"
MAX_WORKERS = 1  # Nombre de requêtes simultanées (à ajuster selon les capacités de votre serveur Odoo)
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


def get_priority_files_from_csv(csv_path):
    """Lit un CSV et extrait une liste de noms de fichiers (.base64) à traiter en priorité."""
    priority_files = set()
    if not csv_path or not os.path.isfile(csv_path):
        if csv_path:
            logging.warning(f"Fichier CSV introuvable : {csv_path}")
        return priority_files

    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            first_line = f.readline()
            delimiter = ";" if ";" in first_line else ","
            f.seek(0)

            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                # Cherche les colonnes selon votre format (Quality Check) ou fallback
                name = (
                    row.get("Quality Check")
                    or row.get("complete_name")
                    or row.get("name")
                )
                if not name and row:
                    name = list(row.values())[0]  # Fallback sur la 1ère colonne

                if name:
                    # Nettoyer les espaces ou retours à la ligne inutiles
                    name = name.strip()
                    # Retire l'extension s'il y en a une, puis force .base64
                    base_name = name.rsplit(".", 1)[0] if "." in name else name
                    b64_name = f"{base_name}.base64"
                    priority_files.add(b64_name)

        logging.info(
            f"{len(priority_files)} fichier(s) prioritaire(s) identifié(s) dans le CSV."
        )
    except Exception as e:
        logging.error(f"Erreur lors de la lecture du CSV {csv_path}: {e}")

    return priority_files


def process_single_file(filename, processed_set):
    """Fonction dédiée au traitement d'un seul fichier, facilitant l'exécution parallèle."""
    # Vérification anti-doublon
    if filename in processed_set:
        logging.info(f"Ignoré (déjà traité) : {filename}")
        return True

    filepath = os.path.join(OCR_OUT_DIR, filename)

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
            # On enregistre le fichier comme traité
            mark_as_processed(filename)

            # Optionnel: supprimer le fichier après un envoi réussi
            # os.remove(filepath)
        else:
            logging.warning(f"Échec ou absence de correspondance pour {filename}.")
        return success
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi de {filename} à Odoo: {e}")
        return False


def run_batch(files_list, processed_set, batch_name):
    """Lance un pool de threads pour une liste donnée de fichiers."""
    if not files_list:
        return

    logging.info(
        f"--- Démarrage de la file {batch_name} ({len(files_list)} fichiers) ---"
    )
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_single_file, filename, processed_set): filename
            for filename in files_list
        }

        for future in as_completed(futures):
            filename = futures[future]
            try:
                future.result()
            except Exception as exc:
                logging.error(
                    f"Une exception critique inattendue s'est produite pour {filename} : {exc}"
                )


def process_base64_files(csv_path=None):
    if not os.path.isdir(OCR_OUT_DIR):
        logging.error(f"Le dossier '{OCR_OUT_DIR}' n'existe pas.")
        return

    files = [f for f in os.listdir(OCR_OUT_DIR) if f.endswith(".base64")]
    if not files:
        logging.info(f"Aucun fichier .base64 trouvé dans '{OCR_OUT_DIR}'.")
        return

    # Chargement de l'historique des fichiers déjà traités
    processed_set = load_processed_history()

    # Filtrer les fichiers qui ne sont pas encore dans l'historique
    files_to_process = [f for f in files if f not in processed_set]

    logging.info(f"{len(files)} fichier(s) au total dans '{OCR_OUT_DIR}'.")
    logging.info(f"{len(files_to_process)} nouveau(x) fichier(s) à traiter.")

    if not files_to_process:
        return

    # Lecture du CSV pour la priorité
    priority_set = get_priority_files_from_csv(csv_path)

    # Séparation en deux files d'attente
    priority_queue = []
    normal_queue = []

    for f in files_to_process:
        if f in priority_set:
            priority_queue.append(f)
        else:
            normal_queue.append(f)

    # Traitement séquentiel des deux files (la prioritaire d'abord, puis la normale)
    run_batch(priority_queue, processed_set, "PRIORITAIRE (CSV)")
    run_batch(normal_queue, processed_set, "NORMALE")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Envoi de fichiers Base64 vers Odoo")
    parser.add_argument(
        "--csv",
        type=str,
        help="Chemin vers un fichier CSV contenant les fichiers à traiter en priorité",
        default=None,
    )
    args = parser.parse_args()

    process_base64_files(csv_path=args.csv)
