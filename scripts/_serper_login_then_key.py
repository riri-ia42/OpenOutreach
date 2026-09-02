"""Login manuel Serper (Chrome normal) puis recuperation auto de la cle.

1. Chrome normal s'ouvre sur serper.dev/login (profil google_profile).
   Richard se connecte (Remember me coche), arrive sur le dashboard, FERME.
2. Patchright rouvre le profil -> /api-key -> lit la cle -> data/serper_key.txt.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

KEY_RE = re.compile(r"^[0-9a-f]{40,64}$", re.I)


def log(msg: str) -> None:
    print(f"[serper] {msg}", flush=True)


def main() -> None:
    log("Chrome ouvert sur serper.dev/login — RICHARD: connecte-toi (Remember me),")
    log("attends le dashboard, puis FERME la fenetre.")
    proc = subprocess.Popen([
        CHROME, f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run", "--no-default-browser-check",
        "https://serper.dev/login",
    ])
    proc.wait()
    time.sleep(2)
    log("Fenetre fermee — lecture de la cle ...")

    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), channel="chrome",
        headless=False, no_viewport=True, chromium_sandbox=True,
    )
    context.set_default_timeout(30000)
    page = context.pages[0] if context.pages else context.new_page()
    key = None
    try:
        for url in ("https://serper.dev/api-key", "https://serper.dev/dashboard"):
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            for tb in page.locator("input").all():
                try:
                    val = (tb.input_value() or "").strip()
                except Exception:
                    continue
                if KEY_RE.match(val):
                    key = val
                    break
            if key:
                break
        if key:
            (BASE_DIR / "data" / "serper_key.txt").write_text(key, encoding="utf-8")
            log("CLE RECUPEREE -> data/serper_key.txt")
        else:
            page.screenshot(path=str(BASE_DIR / "data" / "serper_page.png"), full_page=True)
            (BASE_DIR / "data" / "serper_page.txt").write_text(
                page.inner_text("body"), encoding="utf-8")
            log("Cle non trouvee — voir data/serper_page.png")
    finally:
        context.close()
        pw.stop()


if __name__ == "__main__":
    main()
