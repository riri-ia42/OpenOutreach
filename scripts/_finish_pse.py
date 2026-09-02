"""Fin du setup CSE : Richard cree le moteur a la main (captcha), puis on
recupere le cx automatiquement et on teste l'API en live.

1. Ouvre un Chrome NORMAL (profil google_profile) sur la page de creation PSE.
   Richard : nom "prospection", site "www.linkedin.com/in/*" + Ajouter,
   coche le captcha, clique Creer, FERME la fenetre.
2. Patchright relit la liste des moteurs -> cx.
3. Test live Custom Search avec la cle creee precedemment.
4. Resultat dans data/google_cse_setup_result.json (complete).
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"
RESULT_PATH = BASE_DIR / "data" / "google_cse_setup_result.json"
CREATE_URL = "https://programmablesearchengine.google.com/controlpanel/create"
LIST_URL = "https://programmablesearchengine.google.com/controlpanel/all"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def log(msg: str) -> None:
    print(f"[pse-finish] {msg}", flush=True)


def live_test(key: str, cx: str) -> dict:
    params = urllib.parse.urlencode({
        "key": key, "cx": cx, "q": 'site:linkedin.com/in "menuiserie aluminium"', "num": 3,
    })
    url = f"https://www.googleapis.com/customsearch/v1?{params}"
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"status": resp.status, "items": [i.get("link") for i in data.get("items", [])]}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read().decode("utf-8", errors="replace")[:500]}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "error": str(e)}


def main() -> None:
    log("Ouverture de Chrome sur la page de creation du moteur.")
    log("RICHARD: (1) nom 'prospection' (2) site www.linkedin.com/in/* puis AJOUTER")
    log("(3) coche 'Je ne suis pas un robot' (4) clique CREER (5) FERME Chrome.")
    proc = subprocess.Popen([
        CHROME, f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run", "--no-default-browser-check", CREATE_URL,
    ])
    proc.wait()
    time.sleep(2)
    log("Fenetre fermee, recuperation du cx ...")

    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), channel="chrome",
        headless=False, no_viewport=True, chromium_sandbox=True,
    )
    context.set_default_timeout(30000)
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(LIST_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        engines = []
        for a in page.locator('a[href*="cx="]').all():
            href = a.get_attribute("href") or ""
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            cx = qs.get("cx", [None])[0]
            if cx and cx not in [e["cx"] for e in engines]:
                engines.append({"name": (a.inner_text() or "").strip(), "cx": cx})
        log(f"Moteurs: {engines}")
        result["engines"] = engines
        result["cx"] = engines[0]["cx"] if engines else None
    finally:
        context.close()
        pw.stop()

    if result.get("cx") and result.get("api_key"):
        log("Test live Custom Search ...")
        result["test"] = live_test(result["api_key"], result["cx"])
        log(f"Test: {result['test']}")

    RESULT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    ok = (result.get("test") or {}).get("status") == 200
    log("SUCCES COMPLET" if ok else "INCOMPLET — voir JSON")


if __name__ == "__main__":
    main()
