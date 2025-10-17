# Watcher pour OCR de PDF et conversion en base64

Ce petit utilitaire surveille un répertoire pour détecter des fichiers PDF entrants, tente d'exécuter l'OCR (via ocrmypdf) et écrit deux sorties :

- un PDF OCRisé nommé <nom>\_ocr.pdf dans le dossier de sortie
- un fichier texte <nom>.base64 contenant le PDF (OCRisé si possible) encodé en base64

Le script principal est `watcher_base64_threading.py`.
Depuis la refactorisation, la logique est organisée dans un paquet Python `watcher` (modules `utils.py`, `ocr.py`, `handlers.py`, `cli.py`). Le script garde la même interface et délègue au paquet.

## Pré-requis système

- Debian/Ubuntu :

  - Installez Tesseract et ses langues si nécessaire : <br>
    `sudo apt install tesseract-ocr tesseract-ocr-eng`
  - Installez les dépendances recommandées pour ocrmypdf (ghostscript, qpdf...) : <br>
    `sudo apt install ghostscript qpdf libtiff5`

- ocrmypdf lui-même est requis pour bénéficier de l'OCR. Si vous n'en avez pas besoin, le watcher émettra simplement les octets du PDF original en base64.

## Installation Python

1. Créez un environnement virtuel (recommandé) :

   `python3 -m venv .venv` <br>
   `source .venv/bin/activate`

2. Installez les dépendances Python listées dans `requirements.txt` :

   `pip install -r requirements.txt`

> Note : le paquet `ocrmypdf[watcher]` fournit des dépendances pour la surveillance, mais ce dépôt utilise `watchdog` et `typer` explicitement.

## Utilisation

Exemples d'exécution (depuis la racine du projet) :

1. Utilisation simple :
```
export OCR_INPUT_DIRECTORY=./input-pdfs
export OCR_OUTPUT_DIRECTORY=./output-pdfs
python3 watcher_base64_threading.py --input-dir $OCR_INPUT_DIRECTORY --output-dir $OCR_OUTPUT_DIRECTORY
```

2. Avec variables d'environnement (POSIX) et options :
```
OCR_INPUT_DIRECTORY=./input-pdfs OCR_OUTPUT_DIRECTORY=./output-pdfs python3 watcher_base64_threading.py --initial-scan
```

Options utiles du script :

- `--input-dir` : dossier à surveiller (obligatoire)
- `--output-dir` : dossier de sortie (par défaut `<input-dir>/base64`)
- `--workers` : nombre max de PDFs traités en parallèle
- `--ocr-jobs` : jobs par fichier pour ocrmypdf (réglez à 1 si vous augmentez `--workers`)
- `--initial-scan/--no-initial-scan` : traiter les fichiers existants au démarrage
- `--poll/--no-poll` : forcer l'observer en polling

Le script détecte automatiquement si `ocrmypdf` est installé. S'il ne l'est pas, il écrira les bytes originaux encodés en base64 et ne fera pas d'OCR.

## Fichiers importants

- `watcher_base64_threading.py` : script principal (watcher + OCR + conversion base64) ; aujourd'hui simple wrapper autour de `watcher.cli`
- `watcher/cli.py` : CLI Typer (commande `main`)
- `watcher/handlers.py` : gestionnaire `PdfToBase64Handler`
- `watcher/ocr.py` : fonction `ocr_to_bytes` et dépendances optionnelles
- `watcher/utils.py` : utilitaires `is_within`, `wait_for_file_ready`
- `requirements.txt` : dépendances Python

## Liens utiles

- OCRmyPDF : [Github](https://github.com/ocrmypdf/OCRmyPDF)
- Tesseract : [Github](https://github.com/tesseract-ocr/tesseract)
- Watchdog : [Github](https://github.com/gorakhargosh/watchdog)
- PikePDF : [Github](https://github.com/pikepdf/pikepdf)
- Tutorial OCRmyPDF : [Nutrient](https://www.nutrient.io/blog/how-to-ocr-pdfs-in-linux/)

## Exemple rapide (Windows PowerShell sous WSL)

Dans PowerShell (utilisant WSL) :

    wsl bash -lc "OCR_INPUT_DIRECTORY=./input-pdfs OCR_OUTPUT_DIRECTORY=./output-pdfs python3 watcher_base64_threading.py --input-dir ./input-pdfs --output-dir ./output-pdfs"

## Utilisation avancée (importer comme librairie)

Vous pouvez aussi importer le paquet `watcher` dans votre propre code :

```python
from watcher import app, main, PdfToBase64Handler
# ou encore :
from watcher.cli import app
```

## Remarques

- Le watcher utilise `watchdog` et choisit automatiquement entre `Observer` et `PollingObserver` (utile sur montages réseau ou /mnt).
- Le comportement par défaut est de traiter les fichiers existants à l'initialisation (`--initial-scan` activé).
