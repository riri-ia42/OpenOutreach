"""Probe : numero du projet resolute-button + liste des projets du compte +
liste des cles du projet. Objectif : localiser la cle AIzaSyCI-... (consumer
projects/648364020234 d'apres l'erreur 403).
"""
from __future__ import annotations

from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"

WELCOME = "https://console.cloud.google.com/welcome?project=resolute-button-471512-m3"
CRED = "https://console.cloud.google.com/apis/credentials?project=resolute-button-471512-m3"


def log(msg: str) -> None:
    print(f"[probe] {msg}", flush=True)


def main() -> None:
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), channel="chrome",
        headless=False, no_viewport=True, chromium_sandbox=True,
    )
    context.set_default_timeout(30000)
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(WELCOME, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        body = page.inner_text("body")
        (BASE_DIR / "data" / "welcome.txt").write_text(body, encoding="utf-8")
        for line in body.splitlines():
            if any(k in line.lower() for k in ("numéro", "numero", "number", "resolute")):
                log(f"WELCOME: {line.strip()}")

        page.goto(CRED, wait_until="domcontentloaded")
        page.wait_for_timeout(10000)
        body = page.inner_text("body")
        (BASE_DIR / "data" / "credentials_list.txt").write_text(body, encoding="utf-8")
        page.screenshot(path=str(BASE_DIR / "data" / "credentials_list.png"), full_page=True)
        log("credentials_list.txt + .png ecrits")
    finally:
        context.close()
        pw.stop()


if __name__ == "__main__":
    main()
