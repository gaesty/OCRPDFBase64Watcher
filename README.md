# Watcher OCR PDF -> Base64 (avec envoi Odoo optionnel)

Ce dépôt surveille un dossier, applique l'OCR via `ocrmypdf` quand possible, compresse les PDFs (Ghostscript), génère `<nom>_ocr.pdf` et `<nom>.base64`, et peut envoyer les fichiers encodés à Odoo.

Le code est organisé avec des wrappers scripts en racine et une logique réutilisable dans le package `watcher/`.

## Table des matières

- [Fonctionnalités](#fonctionnalites)
- [Prérequis système](#prerequis-systeme)
- [Installation Python](#installation-python)
- [Démarrage rapide](#demarrage-rapide)
- [Mode Odoo (envoi)](#mode-odoo-envoi)
- [Envoi massif (send_base64_to_odoo.py)](#envoi-massif)
- [Mode CSV (batch)](#mode-csv)
- [Déploiement en service (WSL / systemd)](#deploiement-en-service)
- [Options CLI principales](#options-cli-principales)
- [Sorties & historique](#sorties--historique)
- [Dépannage rapide](#depannage-rapide)
- [Exemples rapides](#exemples-rapides)


## Fonctionnalités

- OCR automatique (fallback sur l'original si OCR impossible).
- Compression Ghostscript (best-effort).
- Fichier d'historique `.processed_history` pour éviter les retraitements.
- Nettoyage automatique en mode Odoo (suppression de `_ocr.pdf` et `.base64` après envoi réussi).
- Mode CSV pour traiter des listes de fichiers avec recherche tolérante des chemins.
- Options avancées : PDF/A (`--output-type pdfa`), JBIG2 (`--jbig2`), workers auto (`--workers-auto`).


## Prérequis système

Sur Debian/Ubuntu, installez au minimum :

```bash
sudo apt install tesseract-ocr tesseract-ocr-eng ghostscript qpdf libtiff5
```

Pour JBIG2 (optionnel) : installez un binaire `jbig2` ou `jbig2enc` disponible dans le `PATH`.


## Installation Python

Créer et activer un virtualenv puis installer les dépendances :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Note : le projet a été testé avec Python 3.x ; des optimisations pour Python 3.14 free-threaded sont documentées dans le code et peuvent améliorer les performances CPU-bound.


## Démarrage rapide

Exemple simple (surveillance d'un dossier) :

```bash
export OCR_INPUT_DIRECTORY=./pdfs
export OCR_OUTPUT_DIRECTORY=./ocr_out   # facultatif, défaut: <input>/base64
python3 watcher_base64_threading.py
```

Sortie attendue : `./ocr_out/<nom>_ocr.pdf` (ou l'original) et `./ocr_out/<nom>.base64`.


## Mode Odoo (envoi)

Variables d'environnement attendues :

```bash
ODOO_URL=https://mon-odoo.com    # /jsonrpc ajouté si absent
ODOO_DATABASE=ma-base            # ou ODOO_DB
ODOO_USER=mon-user
ODOO_API_KEY=mon-api-key         # ou ODOO_PASSWORD
```

Lancer le watcher en mode connecté :

```bash
python3 watcher_base64_threading.py --input-dir ./pdfs --output-dir ./ocr_out
```

Comportement :
- Cherche un `quality.document` portant le même nom, sinon tente de trouver un `aa.worksheet.template` correspondant au préfixe du nom de fichier.
- Si envoi réussi : suppression de `_ocr.pdf` et `.base64`, et historique mis à jour.
- Si échec logique (ex : template introuvable) : fichiers conservés et nom ajouté à l'historique pour éviter les boucles.


## Envoi massif

Si vous avez déjà des `.base64` générés et souhaitez les envoyer massivement vers Odoo :

```bash
python3 send_base64_to_odoo.py
```

Option `--csv` disponible pour fournir une liste de fichiers en entrée.

Le script :
- Utilise les variables Odoo depuis l'environnement.
- Envoie plusieurs fichiers en parallèle (par défaut jusqu'à 5 workers, configurable).
- Écrit un `processed_history.txt` thread-safe : seuls les envois ayant retourné un succès strict y sont inscrits.
- Log détaillé dans `send_base64_to_odoo.log`.


## Mode CSV (batch)

Soumettre un batch via CSV :

```bash
python3 watcher_csv.py \
  --input-dir /mnt/share \
  --output-dir ./ocr_out \
  --csv-file ./files_to_process.csv \
  --csv-only
```

CSV attendu : colonnes `complete_name` et `file_path`. Le script tente plusieurs heuristiques pour résoudre les chemins relatifs/absolus.


## Déploiement en service (WSL / systemd)

Résumé des étapes :

1. Activer `systemd` dans WSL :

```ini
[boot]
systemd=true
```

2. Créer un service systemd (ex : `/etc/systemd/system/ocr-watcher.service`) en injectant les variables d'environnement nécessaires et en utilisant votre venv pour `ExecStart`.

3. Activer et démarrer le service :

```bash
sudo systemctl daemon-reload
sudo systemctl enable ocr-watcher.service
sudo systemctl start ocr-watcher.service
```

Voir la section détaillée et les exemples dans les scripts fournis si besoin.


## Options CLI principales

- `--input-dir` (obligatoire) : dossier à surveiller.
- `--output-dir` : dossier de sortie (par défaut: `<input>/base64`).
- `--workers` : nombre max de PDFs traités en parallèle.
- `--workers-auto` : auto-calcul (`half` ou `full`).
- `--ocr-jobs` : jobs par fichier pour `ocrmypdf`.
- `--output-type {pdf,pdfa}` : sortie classique ou PDF/A-2B.
- `--jbig2 {off,lossless,lossy}` : compression JBIG2 (nécessite binaire).
- `--initial-scan / --no-initial-scan` : contrôler le scan initial.
- `--csv-file` + `--csv-only` : options CSV.
- `--poll / --no-poll` : forcer polling ou inotify.
- `--retries` : tentatives d'attente de stabilité de fichier.
- `--loglevel` : `DEBUG`, `INFO`, `WARNING`, `ERROR`.


## Sorties & historique

- `_ocr.pdf` : résultat OCR (ou original si non disponible).
- `.base64` : encodage du PDF écrit.
- `.processed_history` : historique horodaté dans le dossier de sortie.


## Dépannage rapide

- Activer les logs détaillés :

```bash
python3 watcher_base64_threading.py --loglevel DEBUG
```

- Voir les logs systemd :

```bash
sudo journalctl -u ocr-watcher.service -f
```

- Vérifier les permissions et `.processed_history` si un fichier bouclera.
- Pour améliorer les performances OCR, baissez `--ocr-jobs` quand `--workers` est élevé.


## Exemples rapides

- Local, sans Odoo :

```bash
python3 watcher_base64_threading.py --input-dir ./pdfs --output-dir ./ocr_out --workers 2 --ocr-jobs 1
```

- Odoo + PDF/A + JBIG2 :

```bash
ODOO_URL=https://odoo.example.com ODOO_DATABASE=ma-base ODOO_USER=me ODOO_API_KEY=cle \
  python3 watcher_base64_threading.py --input-dir ./pdfs --output-dir ./ocr_out --output-type pdfa --jbig2 lossless
```

- Batch CSV puis watcher continu :

```bash
python3 watcher_csv.py --input-dir /mnt/share --output-dir ./ocr_out --csv-file ./files_to_process.csv
```


