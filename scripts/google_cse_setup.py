"""Setup Google CSE (cle API + cx) en pilotant la console Google via Patchright.

Pattern identique au login LinkedIn (cf. linkedin_manual_login) :
  Phase A — un Chrome NORMAL s'ouvre sur la console Google avec un profil dedie
            (data/google_profile, separe du profil LinkedIn). Richard se
            connecte avec son Gmail PERSO puis FERME la fenetre.
  Phase B — Patchright reprend le profil authentifie et :
            1. recupere le cx du moteur sur programmablesearchengine.google.com
            2. cree une cle API neuve (sans expiration) sur la console GCP
            3. teste l'API Custom Search en live (200 attendu)
            4. ecrit le resultat dans data/google_cse_setup_result.json

Usage :
    .venv/Scripts/python.exe scripts/google_cse_setup.py            # phases A + B
    .venv/Scripts/python.exe scripts/google_cse_setup.py --skip-login  # phase B seule
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from patchright.sync_api import TimeoutError as PWTimeout
from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"
RESULT_PATH = BASE_DIR / "data" / "google_cse_setup_result.json"
FAIL_SHOT = BASE_DIR / "data" / "google_cse_setup_fail.png"

PROJECT_ID = "resolute-button-471512-m3"
CREDENTIALS_URL = f"https://console.cloud.google.com/apis/credentials?project={PROJECT_ID}"
PSE_LIST_URL = "https://programmablesearchengine.google.com/controlpanel/all"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")


def log(msg: str) -> None:
    print(f"[cse-setup] {msg}", flush=True)


def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    return None


def phase_a_manual_login() -> None:
    """Ouvre un Chrome normal pour que Richard se connecte, attend la fermeture."""
    chrome = find_chrome()
    if not chrome:
        log("ERREUR: Google Chrome introuvable.")
        sys.exit(1)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    log("Ouverture de Chrome sur la console Google (profil dedie).")
    log("RICHARD: connecte-toi avec ton Gmail PERSO, attends que la page")
    log("'Identifiants' s'affiche, puis FERME la fenetre Chrome.")
    proc = subprocess.Popen([
        chrome,
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        CREDENTIALS_URL,
    ])
    proc.wait()
    time.sleep(2)
    log("Fenetre fermee, je prends la main.")


def click_first(page, variants: list, timeout_ms: int = 8000) -> bool:
    """Tente une liste de locators, clique le premier visible."""
    for factory in variants:
        try:
            loc = factory(page).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.click()
            return True
        except PWTimeout:
            continue
        except Exception:
            continue
    return False


def dismiss_consent(page) -> None:
    """Bandeaux cookies/consentement Google (FR/EN), best-effort."""
    click_first(page, [
        lambda p: p.get_by_role("button", name=re.compile("Tout accepter|Accept all", re.I)),
        lambda p: p.get_by_role("button", name=re.compile("^(J'accepte|I agree)$", re.I)),
    ], timeout_ms=3000)


def get_cx(page) -> tuple[str | None, list[dict]]:
    """Liste les moteurs PSE et retourne (cx choisi, tous les moteurs)."""
    log("Recuperation du cx sur programmablesearchengine.google.com ...")
    page.goto(PSE_LIST_URL, wait_until="domcontentloaded")
    dismiss_consent(page)
    page.wait_for_timeout(4000)
    engines: list[dict] = []
    for a in page.locator('a[href*="cx="]').all():
        href = a.get_attribute("href") or ""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        cx = qs.get("cx", [None])[0]
        name = (a.inner_text() or "").strip()
        if cx and cx not in [e["cx"] for e in engines]:
            engines.append({"name": name, "cx": cx})
    log(f"Moteurs trouves: {engines}")
    if not engines:
        return None, []
    for e in engines:
        if "prospection" in e["name"].lower():
            return e["cx"], engines
    return engines[0]["cx"], engines


def create_api_key(page) -> str | None:
    """Cree une cle API sur la console GCP et la retourne."""
    log("Creation d'une cle API sur la console GCP ...")
    page.goto(CREDENTIALS_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(8000)  # console GCP lente a hydrater

    ok = click_first(page, [
        lambda p: p.get_by_role("button", name=re.compile("Creer des identifiants|Créer des identifiants|Create credentials", re.I)),
        lambda p: p.get_by_text(re.compile("Créer des identifiants|Create credentials", re.I)),
    ], timeout_ms=15000)
    if not ok:
        log("ECHEC: bouton 'Creer des identifiants' introuvable.")
        return None
    page.wait_for_timeout(1500)

    ok = click_first(page, [
        lambda p: p.get_by_role("menuitem", name=re.compile("Cl[eé] API|API key", re.I)),
        lambda p: p.get_by_text(re.compile("^Cl[eé] API$|^API key$", re.I)),
    ], timeout_ms=8000)
    if not ok:
        log("ECHEC: entree de menu 'Cle API' introuvable.")
        return None

    # Dialog "Cle API creee" — la cle complete n'apparait que la.
    log("Attente de la generation de la cle ...")
    deadline = time.time() + 45
    while time.time() < deadline:
        page.wait_for_timeout(2000)
        m = API_KEY_RE.search(page.content())
        if m:
            key = m.group(0)
            log(f"Cle creee: {key[:12]}... (complete dans le JSON resultat)")
            click_first(page, [
                lambda p: p.get_by_role("button", name=re.compile("^(Fermer|Close|OK)$", re.I)),
            ], timeout_ms=3000)
            return key
    log("ECHEC: cle non visible apres 45s.")
    return None


def live_test(key: str, cx: str) -> dict:
    """Appel reel Custom Search — verdict definitif."""
    params = urllib.parse.urlencode({
        "key": key, "cx": cx, "q": 'site:linkedin.com/in "menuiserie aluminium"', "num": 3,
    })
    url = f"https://www.googleapis.com/customsearch/v1?{params}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = [i.get("link") for i in data.get("items", [])]
            return {"status": resp.status, "items": items}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        return {"status": e.code, "error": body}
    except Exception as e:  # noqa: BLE001 — script ops one-shot, on remonte tout
        return {"status": None, "error": str(e)}


def main() -> None:
    if "--skip-login" not in sys.argv:
        phase_a_manual_login()

    result: dict = {"cx": None, "api_key": None, "engines": [], "test": None}
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        channel="chrome",
        headless=False,
        no_viewport=True,
        chromium_sandbox=True,
    )
    context.set_default_timeout(30000)
    page = context.pages[0] if context.pages else context.new_page()
    try:
        cx, engines = get_cx(page)
        result["cx"], result["engines"] = cx, engines
        key = create_api_key(page)
        result["api_key"] = key
        if key and cx:
            log("Test live de l'API Custom Search ...")
            result["test"] = live_test(key, cx)
            log(f"Test: {result['test']}")
    except Exception as e:  # noqa: BLE001 — on veut un screenshot + resultat partiel
        log(f"EXCEPTION: {e}")
        try:
            page.screenshot(path=str(FAIL_SHOT), full_page=True)
            log(f"Screenshot: {FAIL_SHOT}")
        except Exception:
            pass
        result["error"] = str(e)
    finally:
        RESULT_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
        log(f"Resultat ecrit: {RESULT_PATH}")
        context.close()
        pw.stop()

    ok = bool(result.get("api_key") and result.get("cx")) and (result.get("test") or {}).get("status") == 200
    log("SUCCES COMPLET" if ok else "INCOMPLET — voir JSON/screenshot")


if __name__ == "__main__":
    main()
