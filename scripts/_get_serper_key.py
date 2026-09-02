"""Recupere la cle API Serper depuis le dashboard (profil google_profile)."""
from __future__ import annotations

import re
from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"

URLS = [
    "https://serper.dev/api-key",
    "https://serper.dev/dashboard",
]
KEY_RE = re.compile(r"^[0-9a-f]{40,64}$", re.I)


def log(msg: str) -> None:
    print(f"[serper] {msg}", flush=True)


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
        for url in URLS:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            # La cle est dans un input readonly sur la page API Key
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
            page.screenshot(path=str(BASE_DIR / "data" / "serper_page.png"), full_page=True)
            (BASE_DIR / "data" / "serper_page.txt").write_text(
                page.inner_text("body"), encoding="utf-8")
        if key:
            (BASE_DIR / "data" / "serper_key.txt").write_text(key, encoding="utf-8")
            log(f"CLE RECUPEREE ({len(key)} caracteres) -> data/serper_key.txt")
        else:
            log("Cle non trouvee — voir data/serper_page.png (login requis ?)")
    finally:
        context.close()
        pw.stop()


if __name__ == "__main__":
    main()
