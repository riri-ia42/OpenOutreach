"""Verifie/active Custom Search API sur resolute-button-471512-m3, puis
reteste la cle 2 (AIzaSyCz2FR...) avec le nouveau cx.
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
LIB_URL = f"https://console.cloud.google.com/apis/library/customsearch.googleapis.com?project={PROJECT_ID}"

KEY2 = "AIzaSyCz2FRjrGoexlabfhqzkwtYaqdfZaFjpbs"
CX = "546b1a8e2a5d941b9"


def log(msg: str) -> None:
    print(f"[enable] {msg}", flush=True)


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
    try:
        page.goto(LIB_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        body = page.inner_text("body")
        (BASE_DIR / "data" / "cse_api_page.txt").write_text(body, encoding="utf-8")
        page.screenshot(path=str(BASE_DIR / "data" / "cse_api_page.png"), full_page=True)

        if re.search(r"API activée|API enabled|Gérer|Manage", body):
            log("Custom Search API DEJA activee sur le projet.")
        else:
            btn = page.get_by_role("button", name=re.compile("^(Activer|Enable)$", re.I)).first
            try:
                btn.wait_for(state="visible", timeout=10000)
                btn.click()
                log("Clic sur Activer — attente ...")
                page.wait_for_timeout(20000)
                page.screenshot(path=str(BASE_DIR / "data" / "cse_api_after.png"), full_page=True)
                log("Active (voir screenshot).")
            except Exception:
                log("Bouton Activer introuvable — voir data/cse_api_page.png")
    finally:
        context.close()
        pw.stop()

    log("Test live cle 2 ...")
    for attempt in range(6):
        result = live_test(KEY2)
        log(f"  tentative {attempt + 1}: {result}")
        if result.get("status") == 200:
            log("SUCCES avec la cle 2 !")
            return
        time.sleep(20)


if __name__ == "__main__":
    main()
