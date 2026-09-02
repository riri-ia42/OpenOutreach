"""Creation de cle API dans resolute-button avec debug pas-a-pas.

La v2 a montre que le clic 'Cle API' du menu echoue silencieusement et que le
regex attrapait la cle interne de la console Google (AIzaSyCI..., projet
Google 648364020234). Ici : on dumpe le menu, on clique precisement, on
attend le dialog 'Cle API creee', et on VERIFIE que la liste passe a 3 cles.

Logs ASCII uniquement (console Windows cp1252).
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

# Cles deja connues (a ne PAS confondre avec une nouvelle)
KNOWN_KEYS = {
    "AIzaSyCI-zsRP85UVOi0DjtiCwWBwQ1djDy741g",  # cle interne console Google
    "AIzaSyAP-jjEJBzmIyKR4F-3XITp8yM9T1gEEI8",  # cle interne developers.google.com
    "AIzaSyB9vKASqiPS-xWAVBy5YlqOJLEvLwpA6iw",  # cle interne panneau creation
    "AIzaSyCz2FRjrGoexlabfhqzkwtYaqdfZaFjpbs",  # cle 2 (10/06)
}
API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")


def log(msg: str) -> None:
    print(f"[key-v3] {msg}", flush=True)


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


def count_keys(page) -> int:
    hrefs = set()
    for a in page.locator('a[href*="/apis/credentials/key/"]').all():
        hrefs.add(a.get_attribute("href") or "")
    return len(hrefs)


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
        n_before = count_keys(page)
        log(f"Cles avant: {n_before}")

        page.get_by_role("button", name=re.compile("Cr[ée]er des identifiants|Create credentials", re.I)).first.click()
        page.wait_for_timeout(2500)
        page.screenshot(path=str(BASE_DIR / "data" / "keyv3_menu.png"))

        items = page.get_by_role("menuitem").all()
        log(f"{len(items)} entrees de menu:")
        for it in items:
            try:
                log(f"  - {it.inner_text().strip()[:60].replace(chr(10), ' / ')}")
            except Exception:
                pass
        target = None
        for it in items:
            try:
                txt = it.inner_text()
            except Exception:
                continue
            if re.search(r"Cl[ée] API|API key", txt, re.I):
                target = it
                break
        if target is None:
            log("ECHEC: pas d'entree 'Cle API' dans le menu.")
            return
        target.click()
        log("Entree 'Cle API' cliquee — panneau de creation ...")
        page.wait_for_timeout(4000)

        # Nouveau panneau lateral : nom + restrictions + bouton 'Creer'
        try:
            name_input = page.locator('input[value="Clé API 3"], input[value="API key 3"]').first
            name_input.wait_for(state="visible", timeout=8000)
            name_input.fill("prospection-ia-cse")
            log("Nom renseigne: prospection-ia-cse")
        except Exception:
            log("Champ nom non trouve (panneau different ?) — on continue")
        page.screenshot(path=str(BASE_DIR / "data" / "keyv3_form.png"), full_page=True)

        create_btn = page.get_by_role("button", name=re.compile("^(Cr[ée]er|Create)$", re.I)).first
        create_btn.wait_for(state="visible", timeout=10000)
        create_btn.click()
        log("Formulaire soumis — attente de la cle ...")

        # Dialog "Cle API creee" : un champ contient la cle complete
        new_key = None
        deadline = time.time() + 60
        while time.time() < deadline and new_key is None:
            page.wait_for_timeout(2000)
            for m in API_KEY_RE.finditer(page.content()):
                if m.group(0) not in KNOWN_KEYS:
                    new_key = m.group(0)
                    break
        page.screenshot(path=str(BASE_DIR / "data" / "keyv3_dialog.png"))
        if not new_key:
            log("ECHEC: aucune NOUVELLE cle apparue en 60s — voir keyv3_dialog.png")
            return
        key = new_key
        log(f"NOUVELLE cle: {key}")
        try:
            page.get_by_role("button", name=re.compile("^(Fermer|Close)$", re.I)).first.click()
        except Exception:
            page.keyboard.press("Escape")
        page.wait_for_timeout(2000)

        page.goto(CRED_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        n_after = count_keys(page)
        log(f"Cles apres: {n_after} (attendu {n_before + 1})")
        page.screenshot(path=str(BASE_DIR / "data" / "keyv3_list.png"), full_page=True)
        if n_after != n_before + 1:
            log("ATTENTION: la cle n'est pas dans la liste du projet !")
    finally:
        context.close()
        pw.stop()

    if key:
        (BASE_DIR / "data" / "keyv3_result.txt").write_text(key, encoding="utf-8")
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
