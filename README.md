# Watcher OCR PDF -> Base64 (avec envoi Odoo optionnel)

Surveille un dossier, applique l'OCR via `ocrmypdf` quand c'est possible, compresse le PDF (Ghostscript), génère un `<nom>_ocr.pdf` et un `<nom>.base64`, et peut envoyer le PDF encodé à Odoo.

Scripts d'entrée (wrappers) : `watcher_base64_threading.py`, `watcher_base64_threading_split.py`, `watcher_csv.py`.
Logique partagée : paquet Python `watcher/` (`cli.py`, `cli_csv.py`, `handlers.py`, `ocr.py`, `utils.py`, `orm_odoo.py`).

## Fonctionnalités clés

- OCR automatique (fallback sur le PDF d'origine si OCR impossible ou absent).
- Compression Ghostscript best-effort.
- Fichier d'historique `.processed_history` dans le dossier de sortie pour éviter les retraitements (format : `YYYY-MM-DD HH:MM:SS : filename`).
- Nettoyage auto en mode Odoo : supprime `_ocr.pdf` et `.base64` après envoi réussi.
- Mode CSV pour traiter une liste de fichiers (avec recherche tolérante des chemins).
- Options avancées : PDF/A (`--output-type pdfa`), JBIG2 (`--jbig2`), auto-calcul des workers (`--workers-auto`).

## Pré-requis système

- Debian/Ubuntu :
  ```bash
  sudo apt install tesseract-ocr tesseract-ocr-eng ghostscript qpdf libtiff5
  ```
- JBIG2 (optionnel, pour `--jbig2`) : binaire `jbig2` ou `jbig2enc` dans le `PATH`.

## Installation Python

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Démarrage rapide (local)

```bash
export OCR_INPUT_DIRECTORY=./pdfs
export OCR_OUTPUT_DIRECTORY=./ocr_out   # facultatif, défaut: <input>/base64
python3 watcher_base64_threading.py
```

Résultat : `./ocr_out/<nom>_ocr.pdf` (ou l'original si OCR échoue) et `./ocr_out/<nom>.base64`.

## Mode connecté Odoo

Variables attendues :

```bash
ODOO_URL=https://mon-odoo.com            # /jsonrpc sera ajouté si absent
ODOO_DATABASE=ma-base                    # ou ODOO_DB
ODOO_USER=mon-user
ODOO_API_KEY=mon-api-key                 # ou ODOO_PASSWORD
```

```bash
python3 watcher_base64_threading.py --input-dir ./pdfs --output-dir ./ocr_out
# ou :
OCR_INPUT_DIRECTORY=./pdfs OCR_OUTPUT_DIRECTORY=./ocr_out python3 watcher_base64_threading.py
python3 watcher_csv.py --input-dir /XXX/X --output-dir /XXX/X --archive-dir /XXX/X --csv-file 'XXX/XX/X' --csv-only --loglevel DEBUG
```

Comportement :
- Cherche un `quality.document` portant le même nom, sinon
- Cherche un `aa.worksheet.template` dont le préfixe correspond au nom du fichier (ex : `CPA...`).
- Si envoi OK : `_ocr.pdf` et `.base64` sont supprimés et le fichier est inscrit dans l'historique.
- Si échec logique (template introuvable) : fichiers conservés mais le nom est quand même inscrit dans `.processed_history` pour éviter une boucle.

## Mode CSV (batch + watcher)

Permet de soumettre une liste de fichiers via CSV en plus (ou à la place) du scan initial.

```bash
python3 watcher_csv.py \
  --input-dir /mnt/share \
  --output-dir ./ocr_out \
  --csv-file ./files_to_process.csv \
  --csv-only            # optionnel : sort après le batch
```

CSV attendu : colonnes `complete_name` et `file_path`.
- Le chemin est essayé tel quel (absolu), puis en retirant progressivement les préfixes pour le rattacher à `--input-dir`, puis via `complete_name` à la racine de `--input-dir`.
- L'historique évite les doublons même en CSV.

## Options principales (CLI `watcher.cli` et `watcher.cli_csv`)

- `--input-dir` (obligatoire) : dossier à surveiller.
- `--output-dir` : dossier de sortie (`<input>/base64` par défaut).
- `--workers` : nombre max de PDFs traités en parallèle.
- `--workers-auto` : auto-calcul (`half`=~50% des CPU par défaut, `full`=100%).
- `--ocr-jobs` : jobs par fichier pour ocrmypdf (si `--workers`>1 et `--ocr-jobs` non fourni, `ocr-jobs` passe à 1 pour éviter la sursouscription).
- `--output-type {pdf,pdfa}` : génère du PDF classique ou PDF/A-2B.
- `--jbig2 {off,lossless,lossy}` : compression JBIG2 (nécessite binaire `jbig2`).
- `--initial-scan / --no-initial-scan` : traiter ou ignorer les PDFs déjà présents (défaut : ON). Peut être forcé via `OCR_INITIAL_SCAN`.
- `--csv-file` + `--csv-only` : options spécifiques au mode CSV.
- `--poll / --no-poll` : forcer le mode polling ou inotify (auto-poll si le chemin commence par `/mnt/`).
- `--retries` : tentatives pour attendre qu'un fichier soit stable.
- `--loglevel` : `DEBUG`, `INFO`, `WARNING`, `ERROR`.

## Sorties et historique

- `_ocr.pdf` : OCR si possible, sinon original. Compression Ghostscript best-effort (`preset=printer`, A4, downsample).
- `.base64` : encodage du même contenu que le PDF écrit.
- `.processed_history` : créé dans le dossier de sortie, horodaté, mis à jour même si l'envoi Odoo échoue (pour éviter la boucle).

## Notes de perf et de stabilité

- Détection du GIL libre si l'API Python est disponible pour ajuster le log de concurrence.
- `wait_for_file_ready` utilise `pikepdf` si présent, sinon vérifie la stabilité de taille.
- Deduplication des événements via un set `_in_flight` pour éviter les doublons quand le FS émet plusieurs événements.

## Arborescence utile

- `watcher_base64_threading.py` / `watcher_base64_threading_split.py` : wrappers CLI.
- `watcher_csv.py` : wrapper CLI CSV.
- `watcher/cli.py` : CLI principale (Typer).
- `watcher/cli_csv.py` : CLI CSV (Typer).
- `watcher/handlers.py` : logique watcher + envoi Odoo + gestion historique.
- `watcher/ocr.py` : OCR via ocrmypdf, PDF/A, JBIG2 optionnel.
- `watcher/utils.py` : utilitaires (readiness, Ghostscript, chemins).
- `watcher/orm_odoo.py` : client JSON-RPC Odoo (quality.document / aa.worksheet.template).
- `requirements.txt` : dépendances Python.

## Dépannage

- Activer les logs détaillés : `python3 watcher_base64_threading.py --loglevel DEBUG`.
- Fichier qui boucle : vérifier `.processed_history` dans le dossier de sortie et les droits d'écriture.
- OCR très lent : réduisez `--ocr-jobs` (1) si vous augmentez `--workers`, ou passez `--workers-auto full` sur un Python free-threaded.
- PDF/A : utilisez `--output-type pdfa` si Odoo ou vos clients exigent du PDF/A-2B.
- JBIG2 : si l'option est ignorée, installez un binaire `jbig2`/`jbig2enc` et placez-le dans le `PATH` (le code essaie aussi `pdfsizeopt/pdfsizeopt_libexec/jbig2`).

## Exemples rapides

- Local, sans Odoo :
  ```bash
  python3 watcher_base64_threading.py --input-dir ./pdfs --output-dir ./ocr_out --workers 2 --ocr-jobs 1
  ```
- Odoo + PDF/A + JBIG2 :
  ```bash
  ODOO_URL=https://odoo.exemple.com ODOO_DATABASE=ma-base ODOO_USER=me ODOO_API_KEY=cle \
    python3 watcher_base64_threading.py --input-dir ./pdfs --output-dir ./ocr_out --output-type pdfa --jbig2 lossless
  ```
- Batch CSV puis watcher continu :
  ```bash
  python3 watcher_csv.py --input-dir /mnt/share --output-dir ./ocr_out --csv-file ./files_to_process.csv
  ```
