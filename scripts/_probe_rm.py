"""Liste tous les projets du compte (resource manager) pour trouver 648364020234."""
from __future__ import annotations

from pathlib import Path

from patchright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_DIR = BASE_DIR / "data" / "google_profile"
URL = "https://console.cloud.google.com/cloud-resource-manager"


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
        body = page.inner_text("body")
        (BASE_DIR / "data" / "resource_manager.txt").write_text(body, encoding="utf-8")
        page.screenshot(path=str(BASE_DIR / "data" / "resource_manager.png"), full_page=True)
        print("[rm] OK", flush=True)
    finally:
        context.close()
        pw.stop()


if __name__ == "__main__":
    main()
