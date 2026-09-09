"""Vérification d'existence d'une adresse AVANT l'envoi d'un cold mail.

Fiche hub validée par Richard le 2026-09-09 : du 26/08 au 05/09, 76 rapports de
non-remise pour ~350 envois (un mail sur cinq), dont 57 rebonds sur 75 envois
pour les adresses `decp_influence` (déduites, trois sur quatre n'existent pas).
Microsoft place le seuil d'alerte à 2 % pour le domaine expéditeur, et c'est
ekoalu.com, le domaine de tout le courrier de l'entreprise, qui encaisse.

Deux temps, sans service payant (le port 25 sortant du TSE est ouvert, vérifié
le 2026-09-09) :
  1. serveur MX du domaine (toutes les sources) — gratuit, quelques ms, cache 24 h ;
  2. sonde SMTP `RCPT TO` (sources déduites `decp_influence` / `decp` seulement)
     avec test « catch-all » : un domaine qui accepte tout n'est pas vérifiable
     et part normalement.

Une adresse sans MX ou refusée est marquée bouncée dans prospection-ia sans
jamais partir, inscrite dans `_partage/exclusions.json` et déposée dans le
drop-file de retour-mail pour que l'antichambre suive. Dans le doute (délai,
greylisting, catch-all, erreur réseau) → on envoie : un faux « inexistant »
bloquerait un vrai prospect. Kill-switch : `EKOALU_EMAIL_VERIFY=0`.
"""
from __future__ import annotations

import logging
import os
import random
import smtplib
import socket
import string
import time

logger = logging.getLogger(__name__)

ENV_VAR = "EKOALU_EMAIL_VERIFY"
PROBE_SOURCES = frozenset({"decp_influence", "decp"})
HELO_DOMAIN = "ekoalu.com"
MAIL_FROM = "richard@ekoalu.com"
SMTP_TIMEOUT = 10.0
MX_TTL_SECONDS = 24 * 3600.0

# Formulations d'un refus « utilisateur inconnu » au RCPT TO (FR/EN, MTA courants).
_UNKNOWN_USER_MARKERS = (
    "5.1.1", "5.1.0", "5.1.6", "5.1.10", "user unknown", "unknown user", "no such user",
    "does not exist", "recipient rejected", "recipient address rejected", "mailbox unavailable",
    "invalid recipient", "unknown recipient", "recipient not found", "user not found",
    "utilisateur inconnu", "n'existe pas", "no mailbox", "not our customer", "no such recipient",
)

# MX de « parking » : le domaine a été abandonné, le parqueur accepte tout puis jette.
# Vu en réel le 2026-09-09 : copas.com (4 contacts en base) → park-mx.above.com, catch-all.
_PARKED_MX_MARKERS = ("park-mx.above.com", "parkingcrew", "sedoparking", "bodis.com", "h-email.net")

_mx_cache: dict[str, tuple[float, str, list[str]]] = {}


def is_parked_mx(hosts: list[str]) -> bool:
    return any(m in h.lower() for h in hosts for m in _PARKED_MX_MARKERS)


def enabled() -> bool:
    """Contrôle actif sauf `EKOALU_EMAIL_VERIFY=0`."""
    return os.environ.get(ENV_VAR, "1").strip() != "0"


def resolve_mx(domain: str) -> tuple[str, list[str]]:
    """('ok', [hôtes MX par préférence]) | ('none', []) | ('unknown', []).

    'none' seulement quand le DNS répond sans ambiguïté qu'il n'y a ni MX ni A
    (un domaine sans MX mais avec un A reçoit encore du courrier, RFC 5321).
    Un délai ou une panne DNS donne 'unknown' : jamais bloquant.
    """
    domain = domain.lower().strip(".")
    cached = _mx_cache.get(domain)
    if cached and time.monotonic() - cached[0] < MX_TTL_SECONDS:
        return cached[1], cached[2]

    import dns.exception
    import dns.resolver

    verdict, hosts = "unknown", []
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5.0)
        hosts = [str(r.exchange).rstrip(".") for r in sorted(answers, key=lambda r: r.preference)]
        hosts = [h for h in hosts if h and h != ""]  # « MX . » = domaine qui refuse le courrier
        verdict = "ok" if hosts else "none"
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        try:
            dns.resolver.resolve(domain, "A", lifetime=5.0)
            verdict, hosts = "ok", [domain]
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            verdict = "none"
        except dns.exception.DNSException:
            verdict = "unknown"
    except dns.exception.DNSException:
        verdict = "unknown"

    _mx_cache[domain] = (time.monotonic(), verdict, hosts)
    return verdict, hosts


def _random_localpart() -> str:
    return "verif-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _looks_unknown_user(code: int, message: bytes | str) -> bool:
    text = (message.decode("utf-8", "replace") if isinstance(message, bytes) else str(message)).lower()
    return code in (550, 551, 553) and any(m in text for m in _UNKNOWN_USER_MARKERS)


def smtp_probe(email: str, mx_hosts: list[str], smtp_factory=smtplib.SMTP) -> tuple[str, str]:
    """('ok' | 'invalid' | 'unknown', détail) par dialogue SMTP jusqu'au RCPT TO.

    Grille de décision (adresse visée / adresse aléatoire du même domaine) :
      250 / 250  → catch-all, invérifiable → unknown
      250 / 5xx  → ok
      5xx / 250  → invalid (le serveur distingue bien les boîtes)
      5xx / 5xx  → invalid seulement si le refus dit « utilisateur inconnu »,
                   sinon c'est une politique (IP bloquée, relais) → unknown
      4xx ou erreur réseau → unknown (greylisting, délai)
    """
    domain = email.rsplit("@", 1)[-1]
    for host in mx_hosts[:2]:
        try:
            with smtp_factory(host, 25, timeout=SMTP_TIMEOUT) as smtp:
                smtp.ehlo(HELO_DOMAIN)
                if smtp.has_extn("starttls"):
                    try:
                        smtp.starttls()
                        smtp.ehlo(HELO_DOMAIN)
                    except smtplib.SMTPException:
                        pass
                code, _ = smtp.mail(MAIL_FROM)
                if code != 250:
                    return "unknown", f"{host}: MAIL FROM {code}"
                code, msg = smtp.rcpt(email)
                if 400 <= code < 500:
                    return "unknown", f"{host}: RCPT {code} (temporaire)"
                random_code, _ = smtp.rcpt(f"{_random_localpart()}@{domain}")
                detail = f"{host}: RCPT {code} / aléatoire {random_code}"
                if code == 250:
                    return ("unknown", detail + " (catch-all)") if random_code == 250 else ("ok", detail)
                if random_code == 250:
                    return "invalid", detail
                if _looks_unknown_user(code, msg):
                    return "invalid", detail + " (utilisateur inconnu)"
                return "unknown", detail + " (politique serveur)"
        except (OSError, socket.timeout, smtplib.SMTPException) as exc:
            logger.info("Sonde SMTP %s via %s impossible : %s", email, host, exc)
            continue
    return "unknown", "aucun MX joignable"


def verify_address(email: str, source: str) -> tuple[str, str]:
    """Verdict avant envoi : 'ok' | 'no_mx' | 'parked' | 'invalid' | 'unknown' | 'skipped', + détail.

    Seuls 'no_mx', 'parked' et 'invalid' bloquent l'envoi. La sonde SMTP ne concerne que
    les sources déduites (PROBE_SOURCES) ; les autres ont déjà reçu du courrier.
    """
    if not enabled():
        return "skipped", "EKOALU_EMAIL_VERIFY=0"
    if "@" not in email:
        return "invalid", "pas de domaine"
    domain = email.rsplit("@", 1)[-1].lower()
    mx_verdict, hosts = resolve_mx(domain)
    if mx_verdict == "none":
        return "no_mx", f"aucun MX ni A pour {domain}"
    if mx_verdict == "unknown":
        return "unknown", f"DNS indisponible pour {domain}"
    if is_parked_mx(hosts):
        return "parked", f"MX de parking pour {domain} ({hosts[0]}) — domaine abandonné"
    if (source or "").lower() not in PROBE_SOURCES:
        return "ok", "MX présent (source non sondée)"
    verdict, detail = smtp_probe(email.lower(), hosts)
    return verdict, detail
