"""Kill-switch du scoping de qualification.

Le routage est ACTIF par defaut. Pour revenir au comportement OpenAI d'origine
(qualifier toute la base) -- en cas d'urgence/regression :
- env var ``EKOALU_SCOPED_QUALIFICATION=0`` (au boot), OU
- fichier sentinel ``data/scoped_qualification_disabled.flag`` (toggle live,
  lu a chaque cycle, pas de redemarrage).
"""
from __future__ import annotations

import os


def scoped_qualification_enabled() -> bool:
    val = os.environ.get("EKOALU_SCOPED_QUALIFICATION", "1").strip().lower()
    if val in ("0", "false", "no"):
        return False
    try:
        from django.conf import settings
        base = getattr(settings, "BASE_DIR", ".")
    except Exception:
        base = "."
    sentinel = os.path.join(base, "data", "scoped_qualification_disabled.flag")
    return not os.path.exists(sentinel)
