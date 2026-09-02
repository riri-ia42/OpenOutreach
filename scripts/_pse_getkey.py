"""Flow 'Obtenir une cle' depuis le panneau PSE (overview du moteur).

C'est ce flow qui INSCRIT un projet aupres de Custom Search JSON API.
On scrolle jusqu'a 'Acces programmatique', on clique le bouton, et dans la
popup on choisit le projet existant 'My First Project' (resolute-button).
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

CX = "546b1a8e2a5d941b9"
OVERVIEW_URL = f"https://programmablesearchengine.google.com/controlpanel/overview?cx={CX}"

API_KEY_RE = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")


def log(msg: str) -> None:
    print(f"[pse-key] {msg}", flush=True)


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


def find_key_in_textboxes(page) -> str | None:
    for tb in page.get_by_role("textbox").all():
        try:
            val = (tb.input_value() or "").strip()
        except Exception:
            continue
        if API_KEY_RE.match(val):
            return val
    return None


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
        page.goto(OVERVIEW_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)
        body = page.inner_text("body")
        (BASE_DIR / "data" / "pse_overview.txt").write_text(body, encoding="utf-8")
        page.screenshot(path=str(BASE_DIR / "data" / "pse_overview.png"), full_page=True)
        log("Overview dumpe — recherche du bouton cle ...")

        btn = page.get_by_role("button", name=re.compile(
            "Obtenir une cl[ée]|Get a key|Commencer|Get started", re.I)).first
        try:
            btn.wait_for(state="visible", timeout=10000)
        except Exception:
            btn = page.get_by_text(re.compile("Obtenir une cl[ée]|Get a key", re.I)).first
            btn.wait_for(state="visible", timeout=10000)
        log("Bouton trouve — clic (popup attendue) ...")
        popup = None
        try:
            with context.expect_page(timeout=15000) as pop_info:
                btn.click()
            popup = pop_info.value
            log("Popup ouverte.")
        except Exception:
            popup = page
            log("Pas de popup — flow inline.")
        popup.wait_for_load_state("domcontentloaded")
        popup.wait_for_timeout(6000)
        popup.screenshot(path=str(BASE_DIR / "data" / "pse_key_1.png"), full_page=True)
        (BASE_DIR / "data" / "pse_key_1.txt").write_text(popup.inner_text("body"), encoding="utf-8")

        # Selecteur de projet (combobox / dropdown henhouse)
        try:
            combo = popup.get_by_role("combobox").first
            combo.wait_for(state="visible", timeout=8000)
            combo.click()
            popup.wait_for_timeout(2000)
            popup.screenshot(path=str(BASE_DIR / "data" / "pse_key_2.png"), full_page=True)
            opt = popup.get_by_role("option", name=re.compile("My First Project", re.I)).first
            opt.wait_for(state="visible", timeout=6000)
            opt.click()
            log("Projet 'My First Project' selectionne.")
            popup.wait_for_timeout(1500)
        except Exception:
            log("Pas de selecteur de projet visible.")
        # Bouton NEXT / Suivant
        for name in ("NEXT", "Suivant", "Next", "DONE"):
            try:
                b = popup.get_by_role("button", name=re.compile(f"^{name}$", re.I)).first
                b.wait_for(state="visible", timeout=4000)
                b.click()
                log(f"Clic {name}.")
                popup.wait_for_timeout(8000)
                break
            except Exception:
                continue
        popup.screenshot(path=str(BASE_DIR / "data" / "pse_key_3.png"), full_page=True)
        try:
            sk = popup.get_by_role("button", name=re.compile("SHOW KEY|Afficher", re.I)).first
            sk.wait_for(state="visible", timeout=5000)
            sk.click()
            popup.wait_for_timeout(3000)
        except Exception:
            pass
        key = find_key_in_textboxes(popup)
        if key:
            log(f"CLE: {key}")
            (BASE_DIR / "data" / "pse_key_result.txt").write_text(key, encoding="utf-8")
        else:
            log("Cle non recuperee — voir pse_key_3.png")
    finally:
        context.close()
        pw.stop()

    if key:
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
