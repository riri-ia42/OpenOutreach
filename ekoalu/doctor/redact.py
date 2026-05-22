"""Redaction de secrets avant tout mail / log doctor.

Tout ce qui matche un pattern secret est remplace par '[REDACTED]'. Le doctor
peut lire le contenu d'un .env ou d'un stack trace contenant une cle : on ne
veut JAMAIS qu'une cle remonte dans un mail (envoye en clair via Graph) ou
dans la DB d'audit (DoctorIncident.diagnosis).
"""
from __future__ import annotations

import re

# Patterns triés du plus specifique au plus generique
_PATTERNS = (
    # Cles Anthropic
    re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"),
    # Refresh tokens OAuth (>= 100 chars base64-url)
    re.compile(r"[A-Za-z0-9_\-]{100,}"),
    # Assignment d'env *_KEY/_SECRET/_TOKEN/_PASSWORD = <valeur>
    re.compile(
        r"([A-Z][A-Z0-9_]*?(?:KEY|SECRET|TOKEN|PASSWORD|PWD))\s*=\s*\S+",
    ),
    # Bearer/Authorization headers
    re.compile(r"(?i)(authorization|bearer)\s*[:=]\s*\S+"),
)

_PLACEHOLDER = "[REDACTED]"


def redact(text: str) -> str:
    """Masque toutes les sequences ressemblant a un secret dans *text*.

    On preserve la clef d'assignment (KEY/SECRET/TOKEN/...) pour qu'on sache
    quelle variable etait presente, mais la valeur est ecrasee.
    """
    if not text:
        return text
    out = text
    # Cas 1 : sk-ant-...
    out = _PATTERNS[0].sub(_PLACEHOLDER, out)
    # Cas 2 : assignment env -> preserve la clef
    out = _PATTERNS[2].sub(r"\1=" + _PLACEHOLDER, out)
    # Cas 3 : Authorization headers
    out = _PATTERNS[3].sub(lambda m: m.group(0).split(m.group(2))[0] + m.group(2) + ": " + _PLACEHOLDER, out)
    # Cas 4 : longues chaines base64-like (tokens) -- en dernier pour ne pas casser
    # les autres patterns ; on exclut les chaines deja redacted
    def _mask_long(m: re.Match) -> str:
        val = m.group(0)
        return _PLACEHOLDER if _PLACEHOLDER not in val else val
    out = _PATTERNS[1].sub(_mask_long, out)
    return out


def redact_dict(data: dict) -> dict:
    """Redact recursivement les valeurs str d'un dict (pour JSON)."""
    if not isinstance(data, dict):
        return data
    out: dict = {}
    for k, v in data.items():
        if isinstance(v, str):
            out[k] = redact(v)
        elif isinstance(v, dict):
            out[k] = redact_dict(v)
        elif isinstance(v, list):
            out[k] = [redact(x) if isinstance(x, str) else redact_dict(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out
