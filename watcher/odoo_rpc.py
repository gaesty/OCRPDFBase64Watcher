import os
from urllib.parse import urlparse
import json
import odoorpc
from dotenv import load_dotenv

load_dotenv(override=True)


def connect_from_env() -> odoorpc.ODOO:
    url = (os.getenv("ODOO_URL") or "").strip()
    if not url:
        raise SystemExit("Set ODOO_URL (ex: https://your-odoo.example.com)")
    p = urlparse(url if "://" in url else f"https://{url}")
    host = p.hostname or url
    use_ssl = (p.scheme or "https").lower() == "https"
    port = p.port or (443 if use_ssl else 8069)
    protocol = "jsonrpc+ssl" if use_ssl else "jsonrpc"

    print(f"Connecting to {host}:{port} ({protocol})")
    try:
        return odoorpc.ODOO(host, port=port, protocol=protocol, timeout=20)
    except Exception as e:
        raise SystemExit(f"Connection failed: {e}")


def main():
    odoo = connect_from_env()

    db = os.getenv("ODOO_DATABASE") or os.getenv("ODOO_DB")
    user = os.getenv("ODOO_USER")
    pwd = os.getenv("ODOO_API_KEY") or os.getenv("ODOO_PASSWORD")
    if not all([db, user, pwd]):
        raise SystemExit(
            "Missing ODOO_DATABASE / ODOO_USER / ODOO_API_KEY(or ODOO_PASSWORD)"
        )

    try:
        odoo.login(db, user, pwd)
    except Exception as e:
        raise SystemExit(f"Login failed: {e}")

    uid = getattr(odoo.env, "uid", None)
    print(f"Logged in (uid={uid})")

    try:
        cpa = odoo.execute_kw(
            "worksheet.template",
            "search_read",
            [[]],
            {},
        )
        print(f"worksheet.template count: {len(cpa)}")
        print(json.dumps(cpa, ensure_ascii=False, indent=2)[:2000])


        
    except Exception as e:
        raise SystemExit(f"Failed to fetch worksheet.template list: {e}")

if __name__ == "__main__":
    main()
