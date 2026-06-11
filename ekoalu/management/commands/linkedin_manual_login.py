"""Login LinkedIn MANUEL dans le profil persistant (amorcage device-trust).

A lancer UNE FOIS (puis a chaque fois que LinkedIn redemande une verification) :
le daemon doit etre ARRETE (le profil Chrome est verrouille par un seul
process). Ouvre le vrai Chrome sur le profil persistant, Richard se connecte a
la main, COCHE "Rester connecte" (→ cookie li_rm = appareil de confiance 1 an),
resout l'eventuel checkpoint, et la commande detecte l'arrivee sur /feed puis
ferme proprement (le profil — cookies/localStorage — est sauvegarde sur disque).

Ensuite le daemon reutilise ce profil : plus aucun login auto, plus de checkpoint.

⚠️ Si le 2FA est actif sur le compte, LinkedIn DESACTIVE "Rester connecte" (li_rm
non pose) → l'appareil ne sera pas durablement de confiance. Pour un device-trust
durable, envisager de desactiver le 2FA (decision Richard).

Usage :
    python manage.py linkedin_manual_login            # attend l'arrivee sur /feed
    python manage.py linkedin_manual_login --timeout 600
"""
from __future__ import annotations

import time
from urllib.parse import unquote

from django.core.management.base import BaseCommand

LOGIN_URL = "https://www.linkedin.com/login"
FEED_URL = "https://www.linkedin.com/feed/"


class Command(BaseCommand):
    help = "Login LinkedIn manuel dans le profil persistant (amorcage device-trust)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout", type=int, default=600,
            help="Secondes d'attente max pour la connexion manuelle (defaut 600)",
        )

    def handle(self, *args, **opts):
        from urllib.parse import urlparse

        from linkedin.browser.login import (
            dismiss_comply_gate,
            launch_persistent_browser,
        )
        from linkedin.conf import LINKEDIN_PROFILE_DIR

        def _on_feed(page) -> bool:
            """True si le path de l'URL est bien /feed (pas un mur de login)."""
            path = urlparse(page.url).path
            if any(b in path for b in ("/login", "/uas/login", "/checkpoint", "/authwall")):
                return False
            return path.startswith("/feed")

        self.stdout.write(self.style.WARNING(
            "\n=== LOGIN LINKEDIN MANUEL (profil persistant) ===\n"
            f"Profil : {LINKEDIN_PROFILE_DIR}\n"
            "⚠️  Le daemon doit etre ARRETE (sinon le profil Chrome est verrouille).\n"
        ))

        page, context, _browser, playwright = launch_persistent_browser()
        try:
            page.goto(FEED_URL)
            dismiss_comply_gate(page)
            page.wait_for_load_state("domcontentloaded")
            if _on_feed(page):
                self.stdout.write(self.style.SUCCESS(
                    "Deja authentifie ✅ — le profil a une session LinkedIn valide."
                    " Rien a faire. (Tu peux quand meme verifier que 'Rester"
                    " connecte' est bien actif.)",
                ))
                time.sleep(3)
                return

            page.goto(LOGIN_URL)
            self.stdout.write(self.style.WARNING(
                "\n>>> CONNECTE-TOI A LA MAIN dans la fenetre Chrome qui vient de s'ouvrir.\n"
                ">>> COCHE 'Rester connecte' / 'Keep me logged in'.\n"
                ">>> Resous l'eventuelle verification de securite (code email/SMS).\n"
                f">>> J'attends l'arrivee sur le fil d'actualite (max {opts['timeout']}s)...\n",
            ))

            deadline = opts["timeout"]
            waited = 0
            while waited < deadline:
                time.sleep(5)
                waited += 5
                try:
                    if _on_feed(page):
                        self.stdout.write(self.style.SUCCESS(
                            f"\n✅ Connexion detectee apres {waited}s. Profil"
                            " device-trust sauvegarde sur disque.\n"
                            ">>> Tu peux maintenant relancer le daemon et cliquer"
                            " ▶ Reprendre sur le dashboard.\n",
                        ))
                        time.sleep(2)
                        return
                except Exception:
                    continue

            self.stdout.write(self.style.ERROR(
                f"\nTimeout apres {deadline}s sans arriver sur /feed."
                " Relance la commande si besoin.",
            ))
        finally:
            try:
                context.close()
                playwright.stop()
            except Exception:
                pass
