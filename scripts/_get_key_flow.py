"""Flow officiel 'Get a Key' de Custom Search JSON API (popup henhouse).

C'est CE flow qui inscrit un projet aupres de l'API (la simple activation
console ne suffit pas -> 'This project does not have the access...').
Page: https://developers.google.com/custom-search/v1/introduction
Bouton 'Get a Key' -> popup -> choisir le projet -> Next -> Show Key.
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

INTRO_URL = "https://developers.google.com/custom-search/v1/introduction"
CX = "546b1a8e2a5d941b9"

API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")


def log(msg: str) -> None:
    print(f"[getkey] {msg}", flush=True)


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
        page.goto(INTRO_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        btn = page.get_by_role("button", name=re.compile("Get a Key|Obtenir une cl[eé]", re.I)).first
        try:
            btn.wait_for(state="visible", timeout=10000)
        except Exception:
            # parfois c'est un lien stylise
            btn = page.get_by_text(re.compile("Get a Key|Obtenir une cl[eé]", re.I)).first
        log("Clic 'Get a Key' ...")
        popup = None
        try:
            with context.expect_page(timeout=15000) as pop_info:
                btn.click()
            popup = pop_info.value
            log("Popup ouverte.")
        except Exception:
            log("Pas de popup — le flow est peut-etre inline.")
            popup = page
        popup.wait_for_load_state("domcontentloaded")
        popup.wait_for_timeout(6000)
        popup.screenshot(path=str(BASE_DIR / "data" / "getkey_1.png"), full_page=True)
        (BASE_DIR / "data" / "getkey_1.txt").write_text(
            popup.inner_text("body"), encoding="utf-8")

        # Etape projet : selectionner 'My First Project' dans le dropdown
        try:
            combo = popup.get_by_role("combobox").first
            combo.wait_for(state="visible", timeout=8000)
            combo.click()
            popup.wait_for_timeout(2000)
            opt = popup.get_by_role("option", name=re.compile("My First Project", re.I)).first
            opt.click()
            log("Projet 'My First Project' selectionne.")
            popup.wait_for_timeout(1500)
        except Exception:
            log("Pas de combobox projet visible (peut-etre etape directe).")
        popup.screenshot(path=str(BASE_DIR / "data" / "getkey_2.png"), full_page=True)

        # Bouton NEXT / SUIVANT
        for name in ("NEXT", "Suivant", "Next"):
            try:
                b = popup.get_by_role("button", name=re.compile(f"^{name}$", re.I)).first
                b.wait_for(state="visible", timeout=5000)
                b.click()
                log(f"Clic {name}.")
                break
            except Exception:
                continue
        popup.wait_for_timeout(10000)
        popup.screenshot(path=str(BASE_DIR / "data" / "getkey_3.png"), full_page=True)

        # SHOW KEY si la cle est masquee
        try:
            sk = popup.get_by_role("button", name=re.compile("SHOW KEY|Afficher", re.I)).first
            sk.wait_for(state="visible", timeout=5000)
            sk.click()
            popup.wait_for_timeout(3000)
        except Exception:
            pass
        content = popup.content()
        m = API_KEY_RE.search(content)
        if m:
            key = m.group(0)
            log(f"CLE OBTENUE: {key}")
            (BASE_DIR / "data" / "getkey_result.txt").write_text(key, encoding="utf-8")
        else:
            popup.screenshot(path=str(BASE_DIR / "data" / "getkey_fail.png"), full_page=True)
            log("Cle non trouvee — voir getkey_3/getkey_fail.png")
    finally:
        context.close()
        pw.stop()

    if key:
        log("Test live ...")
        for attempt in range(6):
            result = live_test(key)
            log(f"  tentative {attempt + 1}: {result}")
            if result.get("status") == 200:
                log("SUCCES COMPLET")
                return
            time.sleep(20)


if __name__ == "__main__":
    main()
