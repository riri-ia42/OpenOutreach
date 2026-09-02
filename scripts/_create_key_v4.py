"""Creation cle API resolute-button — v4.

Lecons v3 : le panneau 'Creer une cle API' exige la selection d'une
restriction d'API (champ avec asterisque) sinon 'Creer' ne fait rien ; et le
DOM de la console contient des cles internes Google (regex interdite).
Ici : dropdown -> Custom Search API -> Creer -> lire la cle via input_value()
des textboxes -> verifier que la liste du projet passe a 3.
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

API_KEY_RE = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")


def log(msg: str) -> None:
    print(f"[key-v4] {msg}", flush=True)


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
        page.goto(CRED_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        n_before = count_keys(page)
        log(f"Cles avant: {n_before}")

        page.get_by_role("button", name=re.compile("Cr[ée]er des identifiants|Create credentials", re.I)).first.click()
        page.wait_for_timeout(2000)
        page.get_by_role("menuitem", name=re.compile("Cl[ée] API|API key", re.I)).first.click()
        page.wait_for_timeout(4000)

        # Dropdown restrictions d'API (obligatoire) -> Custom Search API
        dd = page.get_by_text(re.compile("Aucune API s[ée]lectionn[ée]e|No APIs selected", re.I)).first
        dd.wait_for(state="visible", timeout=10000)
        dd.click()
        page.wait_for_timeout(2000)
        page.screenshot(path=str(BASE_DIR / "data" / "keyv4_dropdown.png"))
        # Filtrer puis cocher 'Custom Search API', puis valider par OK
        filt = page.get_by_placeholder(re.compile("Tapez du texte|filter", re.I)).first
        filt.wait_for(state="visible", timeout=8000)
        filt.fill("Custom Search")
        page.wait_for_timeout(2000)
        row = page.get_by_text(re.compile("^Custom Search API$", re.I)).first
        row.wait_for(state="visible", timeout=8000)
        row.click()
        page.wait_for_timeout(1000)
        page.screenshot(path=str(BASE_DIR / "data" / "keyv4_checked.png"))
        ok_btn = page.get_by_role("button", name=re.compile("^OK$", re.I)).first
        ok_btn.click()
        log("Custom Search API cochee + OK.")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(BASE_DIR / "data" / "keyv4_form.png"))

        create_btn = page.get_by_role("button", name=re.compile("^(Cr[ée]er|Create)$", re.I)).first
        create_btn.scroll_into_view_if_needed()
        create_btn.click()
        log("Creer clique — attente du resultat ...")
        deadline = time.time() + 60
        while time.time() < deadline and key is None:
            page.wait_for_timeout(2000)
            key = find_key_in_textboxes(page)
        page.screenshot(path=str(BASE_DIR / "data" / "keyv4_after.png"))
        if not key:
            log("Pas de cle dans les textboxes — voir keyv4_after.png")
        else:
            log(f"NOUVELLE cle (textbox): {key}")

        page.goto(CRED_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        n_after = count_keys(page)
        log(f"Cles apres: {n_after} (attendu {n_before + 1})")
        page.screenshot(path=str(BASE_DIR / "data" / "keyv4_list.png"), full_page=True)
    finally:
        context.close()
        pw.stop()

    if key:
        (BASE_DIR / "data" / "keyv4_result.txt").write_text(key, encoding="utf-8")
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
