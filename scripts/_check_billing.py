"""Verifie que le projet resolute-button est lie a un compte de facturation,
et fait la liaison si necessaire.
"""
from __future__ import annotations

import re
from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"

PROJECT_ID = "resolute-button-471512-m3"
LINKED_URL = f"https://console.cloud.google.com/billing/linkedaccount?project={PROJECT_ID}"


def log(msg: str) -> None:
    print(f"[billing] {msg}", flush=True)


def main() -> None:
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), channel="chrome",
        headless=False, no_viewport=True, chromium_sandbox=True,
    )
    context.set_default_timeout(30000)
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(LINKED_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        body = page.inner_text("body")
        (BASE_DIR / "data" / "billing_state.txt").write_text(body, encoding="utf-8")
        page.screenshot(path=str(BASE_DIR / "data" / "billing_state.png"), full_page=True)

        if re.search(r"n'est associé à aucun compte|not associated|aucun compte de facturation", body, re.I):
            log("PAS de compte lie -> tentative de liaison ...")
            btn = page.get_by_role("button", name=re.compile("Associer un compte de facturation|Link a billing account", re.I)).first
            btn.wait_for(state="visible", timeout=10000)
            btn.click()
            page.wait_for_timeout(3000)
            # Dialog : selectionner le compte puis "Definir le compte"
            page.screenshot(path=str(BASE_DIR / "data" / "billing_dialog.png"), full_page=True)
            # Le select propose le compte cree par Richard (souvent preselectionne)
            set_btn = page.get_by_role("button", name=re.compile("D[eé]finir le compte|Set account", re.I)).first
            set_btn.wait_for(state="visible", timeout=10000)
            set_btn.click()
            page.wait_for_timeout(8000)
            page.screenshot(path=str(BASE_DIR / "data" / "billing_after.png"), full_page=True)
            log("Liaison cliquee — voir billing_after.png")
        else:
            log("Un compte de facturation semble deja lie (voir billing_state.png)")
            for line in body.splitlines():
                if re.search(r"facturation|billing|compte", line, re.I):
                    log(f"  {line.strip()[:120]}")
    finally:
        context.close()
        pw.stop()


if __name__ == "__main__":
    main()
