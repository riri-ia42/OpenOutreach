"""Passe la restriction d'application des cles API du projet a 'Aucune'.

La cle creee automatiquement a herite d'une restriction 'referer HTTP'
(API_KEY_HTTP_REFERRER_BLOCKED) qui bloque les appels serveur. On ouvre
chaque page d'edition de cle de la console GCP et on coche 'Aucune' + Save.
Puis test live Custom Search.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"
SHOT = BASE_DIR / "data" / "fix_key_fail.png"

PROJECT_ID = "resolute-button-471512-m3"
CREDENTIALS_URL = f"https://console.cloud.google.com/apis/credentials?project={PROJECT_ID}"

API_KEY = "AIzaSyCI-zsRP85UVOi0DjtiCwWBwQ1djDy741g"
CX = "546b1a8e2a5d941b9"


def log(msg: str) -> None:
    print(f"[fix-key] {msg}", flush=True)


def live_test() -> dict:
    import urllib.error
    import urllib.request
    params = urllib.parse.urlencode({
        "key": API_KEY, "cx": CX, "q": 'site:linkedin.com/in "menuiserie aluminium"', "num": 3,
    })
    url = f"https://www.googleapis.com/customsearch/v1?{params}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"status": resp.status, "items": [i.get("link") for i in data.get("items", [])]}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read().decode("utf-8", errors="replace")[:400]}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "error": str(e)}


def main() -> None:
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), channel="chrome",
        headless=False, no_viewport=True, chromium_sandbox=True,
    )
    context.set_default_timeout(30000)
    page = context.pages[0] if context.pages else context.new_page()
    try:
        # Cle creee le 11/06 par google_cse_setup.py
        url = f"https://console.cloud.google.com/apis/credentials/key/bf4bdd27-ca25-47fc-aab5-e9ad2eb83cc4?project={PROJECT_ID}"
        log(f"Edition: {url}")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(12000)
        # Radio "Aucun" / "None" du groupe Restrictions relatives aux applications
        radio = page.get_by_role("radio", name=re.compile("^(Aucun|None)\\b", re.I)).first
        try:
            radio.wait_for(state="visible", timeout=10000)
        except Exception:
            radios = page.get_by_role("radio").all()
            log(f"  radio par nom introuvable — fallback 1er radio sur {len(radios)}")
            if not radios:
                raise RuntimeError("aucun radio sur la page")
            radio = radios[0]
        if radio.is_checked():
            log("  deja 'Aucun' — la restriction vient d'ailleurs.")
        else:
            radio.click()
            page.wait_for_timeout(1000)
            btn = page.get_by_role("button", name=re.compile("^(Enregistrer|Save)$", re.I)).first
            btn.click()
            page.wait_for_timeout(5000)
            log("  restriction retiree + enregistree.")
        page.screenshot(path=str(BASE_DIR / "data" / "fix_key_after.png"), full_page=True)
    except Exception as e:  # noqa: BLE001
        log(f"EXCEPTION: {e}")
        try:
            page.screenshot(path=str(SHOT), full_page=True)
            log(f"Screenshot: {SHOT}")
        except Exception:
            pass
    finally:
        context.close()
        pw.stop()

    log("Test live (la propagation peut prendre ~1-5 min) ...")
    import time
    for attempt in range(6):
        result = live_test()
        log(f"  tentative {attempt + 1}: {result.get('status')}")
        if result.get("status") == 200:
            log(f"SUCCES: {result['items']}")
            return
        time.sleep(30)
    log(f"Toujours pas 200 apres 3 min: {result}")


if __name__ == "__main__":
    main()
