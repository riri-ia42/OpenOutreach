"""Screenshot + dump texte de la page d'edition d'une cle API GCP."""
from __future__ import annotations

import sys
from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"

KEY_ID = sys.argv[1] if len(sys.argv) > 1 else "6dc7ba5b-feba-4793-84d2-402bd459e121"
URL = f"https://console.cloud.google.com/apis/credentials/key/{KEY_ID}?project=resolute-button-471512-m3"


def main() -> None:
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), channel="chrome",
        headless=False, no_viewport=True, chromium_sandbox=True,
    )
    context.set_default_timeout(30000)
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(12000)
        page.screenshot(path=str(BASE_DIR / "data" / "key_page.png"), full_page=True)
        body = page.inner_text("body")
        (BASE_DIR / "data" / "key_page.txt").write_text(body, encoding="utf-8")
        print("[shot] OK — data/key_page.png + key_page.txt", flush=True)
        radios = page.get_by_role("radio").all()
        print(f"[shot] {len(radios)} radios role=radio", flush=True)
        for r in radios[:10]:
            print(f"  - aria-label={r.get_attribute('aria-label')!r}", flush=True)
    finally:
        context.close()
        pw.stop()


if __name__ == "__main__":
    main()
