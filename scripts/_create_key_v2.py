"""Cree une cle API dans resolute-button-471512-m3 et VERIFIE qu'elle
apparait bien dans la liste du projet (la creation du matin a fui vers un
projet fantome). Puis test live.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"

PROJECT_ID = "resolute-button-471512-m3"
CRED_URL = f"https://console.cloud.google.com/apis/credentials?project={PROJECT_ID}"
CX = "546b1a8e2a5d941b9"

API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")


def log(msg: str) -> None:
    print(f"[key-v2] {msg}", flush=True)


def live_test(key: str) -> dict:
    import urllib.error
    import urllib.request
    params = urllib.parse.urlencode({
        "key": key, "cx": CX, "q": 'site:linkedin.com/in "menuiserie aluminium"', "num": 3,
    })
    url = f"https://www.googleapis.com/customsearch/v1?{params}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"status": resp.status, "items": [i.get("link") for i in data.get("items", [])]}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "error": e.read().decode("utf-8", errors="replace")[:300]}
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
    key = None
    try:
        page.goto(CRED_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)

        # Verifier qu'on est bien sur le bon projet (header "My First Project")
        header = page.inner_text("body")[:3000]
        if "My First Project" not in header:
            log("ATTENTION: header sans 'My First Project' — screenshot et stop")
            page.screenshot(path=str(BASE_DIR / "data" / "keyv2_badproject.png"), full_page=True)
            return

        page.get_by_role("button", name=re.compile("Cr[ée]er des identifiants|Create credentials", re.I)).first.click()
        page.wait_for_timeout(1500)
        page.get_by_role("menuitem", name=re.compile("Cl[ée] API|API key", re.I)).first.click()
        log("Creation demandee — attente de la cle ...")
        deadline = time.time() + 45
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            m = API_KEY_RE.search(page.content())
            if m:
                key = m.group(0)
                break
        if not key:
            page.screenshot(path=str(BASE_DIR / "data" / "keyv2_fail.png"), full_page=True)
            log("ECHEC creation — voir keyv2_fail.png")
            return
        log(f"Cle obtenue: {key}")
        # Fermer le dialog
        try:
            page.get_by_role("button", name=re.compile("^(Fermer|Close)$", re.I)).first.click()
            page.wait_for_timeout(2000)
        except Exception:
            page.keyboard.press("Escape")
            page.wait_for_timeout(2000)

        # VERIFICATION : recharger la liste et compter les cles
        page.goto(CRED_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        body = page.inner_text("body")
        n_keys = len(page.locator('a[href*="/apis/credentials/key/"]').all())
        log(f"Liste rechargee: {n_keys} cle(s) visibles dans {PROJECT_ID}.")
        page.screenshot(path=str(BASE_DIR / "data" / "keyv2_list.png"), full_page=True)
        if n_keys < 3:
            log("⚠ La nouvelle cle ne semble PAS dans ce projet (fuite comme ce matin ?)")
    finally:
        context.close()
        pw.stop()

    if key:
        (BASE_DIR / "data" / "keyv2_result.txt").write_text(key, encoding="utf-8")
        log("Test live ...")
        for attempt in range(8):
            result = live_test(key)
            log(f"  tentative {attempt + 1}: {result}")
            if result.get("status") == 200:
                log("SUCCES COMPLET")
                return
            time.sleep(20)


if __name__ == "__main__":
    main()
