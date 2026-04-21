import json
import logging
import os
import time
from urllib import error, request

from dotenv import load_dotenv

load_dotenv()

_UID = None


def ensure_jsonrpc_url(url: str | None) -> str:
    if not url:
        return ""
    url = url.strip().rstrip("/")
    if not url.endswith("jsonrpc"):
        url = url + "/jsonrpc"
    return url


def json_rpc(url, params, timeout=15, retries=3, backoff=0.75):
    data = {
        "jsonrpc": "2.0",
        "method": "call",
        "params": params,
        "id": 1,
    }
    body = json.dumps(data).encode()
    req = request.Request(
        url=url, data=body, headers={"Content-Type": "application/json"}
    )
    attempt = 0
    while True:
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                reply = json.loads(resp.read().decode("utf-8"))
            if reply.get("error"):
                raise Exception(reply["error"])
            return reply["result"]
        except error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < retries:
                attempt += 1
                time.sleep(backoff * attempt)
                continue
            raise
        except error.URLError:
            if attempt < retries:
                attempt += 1
                time.sleep(backoff * attempt)
                continue
            raise


def get_connection_params():
    rpc_url = ensure_jsonrpc_url(os.getenv("ODOO_URL"))
    dbname = os.getenv("ODOO_DATABASE") or os.getenv("ODOO_DB")
    login = os.getenv("ODOO_USER")
    password = os.getenv("ODOO_API_KEY") or os.getenv("ODOO_PASSWORD")
    return rpc_url, dbname, login, password


def get_uid():
    global _UID
    if _UID is not None:
        return _UID

    rpc_url, dbname, login, password = get_connection_params()
    if not all([rpc_url, dbname, login, password]):
        logging.error("Missing ODOO_URL / ODOO_DATABASE / ODOO_USER / ODOO_API_KEY")
        return None

    try:
        _UID = json_rpc(
            rpc_url,
            {"service": "common", "method": "login", "args": [dbname, login, password]},
        )
        return _UID
    except Exception as e:
        logging.error(f"Odoo login failed: {e}")
        return None


def rpc(model, method, *args, **kwargs):
    uid = get_uid()
    if not uid:
        raise Exception("Not connected to Odoo")

    rpc_url, dbname, _, password = get_connection_params()

    return json_rpc(
        rpc_url,
        {
            "service": "object",
            "method": "execute_kw",
            "args": [dbname, uid, password, model, method, list(args), kwargs or {}],
        },
    )


def send_pdf_to_odoo(filename: str, b64_content: str) -> bool:
    """
    Sends the base64 PDF to Odoo.
    Returns True if successful, False if logic failed (e.g. template not found).
    """
    try:
        # 1. Search quality.document
        docs = rpc(
            "quality.document",
            "search_read",
            [["name", "=", filename]],
            fields=["id", "name"],
            limit=1,
        )

        if docs:
            doc_id = docs[0]["id"]
            logging.info(f"Found quality.document {doc_id} for {filename}. Updating...")
            rpc("quality.document", "write", [doc_id], {"datas": b64_content})
            logging.info(f"Updated quality.document {doc_id}.")
            return True

        # 2. Not found, search quality.check
        # Extract prefix: CPA24051600003 from CPA24051600003_20251209075620_ocr.pdf or CPA00032700002.pdf
        prefix = filename.split("_")[0]
        if prefix.lower().endswith(".pdf"):
            prefix = prefix[:-4]

        templates = rpc(
            "aa.worksheet.template",
            "search_read",
            [["display_name", "=", prefix]],
            limit=1,
        )

        if templates:
            template = templates[0]
            template_id = template["id"]
            logging.info(
                f"Found aa.worksheet.template {template_id} for {prefix}. Creating quality.document..."
            )

            vals = {
                "name": filename,
                "res_model": "aa.worksheet.template",
                "res_id": template_id,
                "datas": b64_content,
                "type": "binary",
                "attached_on_ws": "worksheet",
            }

            new_id = rpc("quality.document", "create", vals)
            logging.info(f"Created quality.document {new_id}.")
            return True
        else:
            logging.warning(
                f"Could not find aa.worksheet.template for prefix {prefix}. PDF not sent to Odoo."
            )
            return False

    except Exception as e:
        logging.error(f"Failed to send PDF to Odoo: {e}")
        # En cas d'exception technique (réseau, auth...), on considère aussi que c'est un échec
        # et on ne veut probablement pas supprimer les fichiers.
        raise e
