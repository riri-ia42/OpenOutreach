"""Tente d'activer Custom Search API sur le projet 648364020234 (Default
Gemini Project) via le lien direct donne par l'erreur 403, puis teste la cle
AIzaSyCI avec Referer console.cloud.google.com.
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

ENABLE_URL = "https://console.developers.google.com/apis/api/customsearch.googleapis.com/overview?project=721724668570"
API_KEY = "AIzaSyAP-jjEJBzmIyKR4F-3XITp8yM9T1gEEI8"
CX = "546b1a8e2a5d941b9"


def log(msg: str) -> None:
    print(f"[gem-enable] {msg}", flush=True)


def live_test() -> dict:
    import urllib.error
    import urllib.request
    params = urllib.parse.urlencode({
        "key": API_KEY, "cx": CX, "q": 'site:linkedin.com/in "menuiserie aluminium"', "num": 3,
    })
    url = f"https://www.googleapis.com/customsearch/v1?{params}"
    req = urllib.request.Request(url, headers={"Referer": "https://console.cloud.google.com/"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
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
    try:
        page.goto(ENABLE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(12000)
        body = page.inner_text("body")
        (BASE_DIR / "data" / "gem_enable.txt").write_text(body, encoding="utf-8")
        page.screenshot(path=str(BASE_DIR / "data" / "gem_enable.png"), full_page=True)
        if re.search(r"autorisation|permission|access", body, re.I) and "Activer" not in body and "Enable" not in body:
            log("Acces refuse probable — voir data/gem_enable.png")
        btn = page.get_by_role("button", name=re.compile("^(Activer|Enable)$", re.I)).first
        try:
            btn.wait_for(state="visible", timeout=8000)
            btn.click()
            log("Clic Activer — attente 20s ...")
            page.wait_for_timeout(20000)
            page.screenshot(path=str(BASE_DIR / "data" / "gem_enable_after.png"), full_page=True)
        except Exception:
            log("Pas de bouton Activer visible.")
    finally:
        context.close()
        pw.stop()

    log("Test live (Referer console.cloud.google.com) ...")
    for attempt in range(8):
        result = live_test()
        log(f"  tentative {attempt + 1}: {result.get('status')}")
        if result.get("status") == 200:
            log(f"SUCCES: {result['items']}")
            return
        time.sleep(20)
    log(f"Echec final: {result}")


if __name__ == "__main__":
    main()
