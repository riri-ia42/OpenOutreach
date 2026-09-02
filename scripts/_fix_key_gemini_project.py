"""Retire la restriction referer de la cle creee aujourd'hui (11 juin) dans le
projet 648364020234 (Default Gemini Project), sans toucher aux autres cles.
Puis test live Custom Search en boucle.
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

PROJECT = "648364020234"
CRED_URL = f"https://console.cloud.google.com/apis/credentials?project={PROJECT}"

API_KEY = "AIzaSyCI-zsRP85UVOi0DjtiCwWBwQ1djDy741g"
CX = "546b1a8e2a5d941b9"


def log(msg: str) -> None:
    print(f"[fix-gem] {msg}", flush=True)


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
        page.goto(CRED_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(12000)
        page.screenshot(path=str(BASE_DIR / "data" / "gemini_creds.png"), full_page=True)
        (BASE_DIR / "data" / "gemini_creds.txt").write_text(
            page.inner_text("body"), encoding="utf-8")

        hrefs = []
        for a in page.locator('a[href*="/apis/credentials/key/"]').all():
            href = a.get_attribute("href") or ""
            if href and href not in hrefs:
                hrefs.append(href)
        log(f"{len(hrefs)} cle(s) dans le projet {PROJECT}.")

        fixed = False
        for href in hrefs:
            url = urllib.parse.urljoin("https://console.cloud.google.com", href)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(10000)
            body = page.inner_text("body")
            # Date de creation du jour ? (page FR : "11 juin 2026")
            created_today = bool(re.search(r"11\s+juin\s+2026|June\s+11,\s+2026", body))
            name_input = page.locator("input").first
            name = ""
            try:
                name = name_input.input_value()
            except Exception:
                pass
            log(f"Cle {name!r} — creee aujourd'hui: {created_today} ({url})")
            if not created_today:
                continue
            radio = page.get_by_role("radio", name=re.compile("^(Aucun|None)\\b", re.I)).first
            try:
                radio.wait_for(state="visible", timeout=10000)
            except Exception:
                radios = page.get_by_role("radio").all()
                if not radios:
                    log("  pas de radio, skip")
                    continue
                radio = radios[0]
            if radio.is_checked():
                log("  restriction d'application deja 'Aucun'.")
            else:
                radio.click()
                page.wait_for_timeout(1000)
                page.get_by_role("button", name=re.compile("^(Enregistrer|Save)$", re.I)).first.click()
                page.wait_for_timeout(5000)
                log("  restriction retiree + enregistree.")
            page.screenshot(path=str(BASE_DIR / "data" / "gemini_key_after.png"), full_page=True)
            fixed = True

        if not fixed:
            log("AUCUNE cle du jour trouvee — voir data/gemini_creds.png")
    finally:
        context.close()
        pw.stop()

    log("Test live (propagation possible ~1-5 min) ...")
    for attempt in range(8):
        result = live_test()
        log(f"  tentative {attempt + 1}: {result.get('status')}")
        if result.get("status") == 200:
            log(f"SUCCES: {result['items']}")
            return
        time.sleep(30)
    log(f"Toujours pas 200: {result}")


if __name__ == "__main__":
    main()
