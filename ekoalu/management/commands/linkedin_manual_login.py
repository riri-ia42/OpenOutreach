"""Login LinkedIn MANUEL dans le profil persistant (amorcage device-trust).

⚠️ Methode VALIDEE 11/06 : on ouvre un Chrome **NORMAL** (pas pilote par
Patchright/CDP) sur le profil automation. Un Chrome pilote se fait bloquer la
page de login par LinkedIn (spinner infini = detection d'automatisation). Un
Chrome normal se connecte sans probleme ; ensuite le daemon (Patchright)
reutilise le profil DEJA authentifie → plus jamais de page de login.

A lancer UNE FOIS (puis quand LinkedIn redemande une verif / quand la session
expire). Le daemon doit etre ARRETE (le profil Chrome est verrouille par un
seul process).

Etapes (la commande te guide) :
1. Une fenetre Chrome normale s'ouvre sur la page de login LinkedIn.
2. Tu te connectes a la main, COCHE "Rester connecte" (→ cookie li_rm, device
   trust 1 an — sauf si 2FA actif, qui le desactive).
3. Tu vas jusqu'a ton fil d'actualite, puis tu FERMES la fenetre Chrome.
4. La commande verifie que le profil est bien authentifie (Patchright → /feed).

Usage :
    python manage.py linkedin_manual_login
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from django.core.management.base import BaseCommand

LOGIN_URL = "https://www.linkedin.com/login"


def _find_chrome() -> str | None:
    """Localise le binaire Google Chrome (pas le Chromium de Patchright)."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return shutil.which("chrome") or shutil.which("chrome.exe")


class Command(BaseCommand):
    help = "Login LinkedIn manuel (Chrome normal) dans le profil persistant automation."

    def handle(self, *args, **opts):
        from linkedin.browser.login import is_authenticated, launch_persistent_browser
        from linkedin.conf import LINKEDIN_PROFILE_DIR

        self.stdout.write(self.style.WARNING(
            "\n=== LOGIN LINKEDIN MANUEL (profil persistant) ===\n"
            f"Profil : {LINKEDIN_PROFILE_DIR}\n"
            "⚠️  Le daemon doit etre ARRETE (sinon le profil Chrome est verrouille).\n"
        ))

        chrome = _find_chrome()
        if not chrome:
            self.stdout.write(self.style.ERROR(
                "Google Chrome introuvable. Installer Chrome ou se connecter a la main"
                f" via : chrome.exe --user-data-dir=\"{LINKEDIN_PROFILE_DIR}\" {LOGIN_URL}",
            ))
            return

        LINKEDIN_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self.stdout.write(
            ">>> Ouverture d'un Chrome NORMAL sur la page de login LinkedIn.\n"
            ">>> Connecte-toi A LA MAIN, COCHE 'Rester connecte', va sur ton fil,\n"
            ">>> puis FERME la fenetre Chrome quand c'est fait.\n",
        )
        proc = subprocess.Popen([
            chrome,
            f"--user-data-dir={LINKEDIN_PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            LOGIN_URL,
        ])

        self.stdout.write(">>> J'attends que tu fermes la fenetre Chrome...\n")
        proc.wait()  # bloque jusqu'a fermeture de Chrome par Richard
        time.sleep(2)  # laisse Chrome liberer le profil

        # Verification via Patchright (le navigateur du daemon).
        self.stdout.write(">>> Verification de l'authentification du profil...\n")
        page, context, _browser, pw = launch_persistent_browser()
        try:
            if is_authenticated(page):
                cookies = context.cookies()
                names = {c["name"] for c in cookies if "linkedin" in c.get("domain", "")}
                li_rm = "li_rm" in names
                self.stdout.write(self.style.SUCCESS(
                    f"\n✅ Profil AUTHENTIFIE ({len(names)} cookies LinkedIn)."
                    f" 'Rester connecte' (li_rm) : {'OUI' if li_rm else 'NON'}.\n"
                ))
                if not li_rm:
                    self.stdout.write(self.style.WARNING(
                        "⚠️  li_rm absent : soit 'Rester connecte' non coche, soit 2FA"
                        " actif (LinkedIn le desactive alors). La session expirera dans"
                        " quelques semaines → il faudra refaire ce login.\n",
                    ))
                self.stdout.write(
                    ">>> Tu peux relancer le daemon + cliquer ▶ Reprendre sur le dashboard.\n",
                )
            else:
                self.stdout.write(self.style.ERROR(
                    "\n❌ Profil PAS authentifie. Relance la commande et verifie d'etre"
                    " bien arrive sur ton fil avant de fermer Chrome.\n",
                ))
        finally:
            context.close()
            pw.stop()
