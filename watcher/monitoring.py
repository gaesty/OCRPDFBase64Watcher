import urllib.request
import urllib.parse
import shutil
import os

KUMA_BASE_URL = os.getenv("KUMA_PUSH_URL")

def send_kuma_push(status: str, message: str) -> None:
    """
    Envoie le statut et le message dynamique à Uptime Kuma.
    """
    if not KUMA_BASE_URL:
        print("Avertissement : KUMA_PUSH_URL non définie dans le .env")
        return

    truncated_message = message[:150]
    encoded_msg = urllib.parse.quote(truncated_message)
    push_url = f"{KUMA_BASE_URL}?status={status}&msg={encoded_msg}"
    
    try:
        urllib.request.urlopen(push_url, timeout=5)
    except Exception as e:
        print(f"Échec critique de l'envoi à Kuma : {e}")

def verifier_espace_disque(chemin_dossier: str = "/mnt/d", minimum_go: float = 2.0) -> None:
    """
    Vérifie l'espace disque restant.
    Lève une exception ValueError si l'espace est insuffisant.
    """
    total, utilise, libre = shutil.disk_usage(chemin_dossier)
    libre_go = libre / (1024 ** 3)
    
    if libre_go < minimum_go:
        raise ValueError(f"Espace disque critique : {libre_go:.2f} Go restants sur '{chemin_dossier}'")