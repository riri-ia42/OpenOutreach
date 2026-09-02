"""Sonde programmablesearchengine.google.com : liste moteurs + screenshot.

Si aucun moteur n'existe, tente d'en CREER un ("prospection", restreint a
linkedin.com/in/*) puis recupere le cx.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"
SHOT = BASE_DIR / "data" / "pse_probe.png"
OUT = BASE_DIR / "data" / "pse_probe.json"

LIST_URL = "https://programmablesearchengine.google.com/controlpanel/all"
CREATE_URL = "https://programmablesearchengine.google.com/controlpanel/create"


def log(msg: str) -> None:
    print(f"[pse] {msg}", flush=True)


def extract_engines(page) -> list[dict]:
    engines = []
    for a in page.locator('a[href*="cx="]').all():
        href = a.get_attribute("href") or ""
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        cx = qs.get("cx", [None])[0]
        if cx and cx not in [e["cx"] for e in engines]:
            engines.append({"name": (a.inner_text() or "").strip(), "cx": cx})
    return engines


def main() -> None:
    result: dict = {"engines": [], "created": False, "url": None, "page_text": None}
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), channel="chrome",
        headless=False, no_viewport=True, chromium_sandbox=True,
    )
    context.set_default_timeout(30000)
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(LIST_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)  # SPA lente
        result["engines"] = extract_engines(page)
        result["url"] = page.url
        log(f"URL: {page.url}")
        log(f"Moteurs: {result['engines']}")

        if not result["engines"] and "--create" in sys.argv:
            log("Aucun moteur -> tentative de creation ...")
            page.goto(CREATE_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            # Champ nom
            name_input = page.locator('input[type="text"]').first
            name_input.fill("prospection")
            # Option "rechercher sur des sites specifiques" : champ site
            inputs = page.locator('input[type="text"]').all()
            if len(inputs) >= 2:
                inputs[1].fill("www.linkedin.com/in/*")
            page.wait_for_timeout(1000)
            btn = page.get_by_role("button", name=re.compile("Cr[eé]er|Create", re.I)).first
            btn.click()
            page.wait_for_timeout(8000)
            result["created"] = True
            result["url"] = page.url
            # cx dans l'URL de la page de succes ou retour liste
            m = re.search(r"cx=([0-9a-f]+)", page.url)
            if m:
                result["engines"] = [{"name": "prospection", "cx": m.group(1)}]
            else:
                page.goto(LIST_URL, wait_until="domcontentloaded")
                page.wait_for_timeout(8000)
                result["engines"] = extract_engines(page)
            log(f"Apres creation -> moteurs: {result['engines']}")

        result["page_text"] = page.inner_text("body")[:2000]
    except Exception as e:  # noqa: BLE001
        log(f"EXCEPTION: {e}")
        result["error"] = str(e)
    finally:
        try:
            page.screenshot(path=str(SHOT), full_page=True)
        except Exception:
            pass
        OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"Resultat: {OUT} / screenshot: {SHOT}")
        context.close()
        pw.stop()


if __name__ == "__main__":
    main()
