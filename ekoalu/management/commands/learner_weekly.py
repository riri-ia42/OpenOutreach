"""Learner hebdomadaire des cold mails (fiche hub #139, 09/09/2026).

Une fois par semaine (le dimanche, ordonnanceur du parc), un seul appel à
Claude Fable 5.1 lit TOUT le corpus du canal cold mail — mails envoyés avec
leur variante, réponses reçues et leur intention, corrections et refus de
Richard — et propose une variante de prompt v3 argumentée, à opposer à la
variante en production.

La v3 n'entre JAMAIS seule en production : elle est écrite dans
data/prompt_variants/proposals/v3_<date>.md (+ .txt = prompt seul), un
événement est poussé au hub, et Richard l'active en la copiant dans
data/prompt_variants/ et en la déclarant dans active.json (cf. prompts.py).

    python manage.py learner_weekly [--dry-run] [--days 120] [--max-mails 600]

Coût attendu : 3 à 4 $ par passe (entrée ~300 k tokens). Le budget_guard
quotidien (4 $/j) s'applique via le wrapper llm_usage : lancer le dimanche,
jour sans cold mail.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

logger = logging.getLogger(__name__)

LEARNER_MODEL = "claude-fable-5-1"
MAX_OUTPUT_TOKENS = 16000

SYSTEM_PROMPT = """Tu es le responsable commercial d'EKOALU, fabricant de menuiseries techniques
(aluminium, acier, bois technique) à Chasselay (69), 15 personnes, atelier intégré,
qui conçoit, fabrique et livre mais ne pose pas. Tu analyses le corpus complet du
canal cold mail pour écrire une NOUVELLE variante de prompt système (v3) destinée
au générateur de cold mails, à opposer à la variante en production.

Règles non négociables :
- Reprendre EXACTEMENT le squelette de la variante en production (mêmes marqueurs,
  dont le marqueur littéral {signature_block} qui doit apparaître tel quel).
- Style Richard Gros : phrases courtes, chiffres, termes techniques (EI30/60/120,
  DENFC, BC1-4, Rw, FDES, RE2020), aucun jargon commercial, aucune flatterie,
  aucun tiret cadratin ni emoji ni puce markdown, clôture « Bien à vous ».
- Jamais « on pose ». Gammes citables : Hydro (Wicona, SAPA, Technal), Jansen.
  Ne jamais citer Cortizo ni Sepalumic.
- Aucun engagement chiffré de délai (pas de « 48 h ») : réponse rapide selon la
  taille et la technicité du dossier.

Réponds en français avec exactement ces trois sections, dans cet ordre :
## Analyse
(ce qui distingue les mails qui ont obtenu une réponse réelle des autres, par
segment source / NAF / département, et ce que les corrections de Richard répètent)
## Prompt v3
(le prompt complet, entre une ligne ```prompt et une ligne ``` ; il doit contenir
{signature_block})
## Mots-clés sourcing
(10 à 20 mots-clés ou exclusions pour le sourcing Serper, un par ligne)
"""


def _proposals_dir() -> Path:
    from ekoalu.email_generator.ab_rule import variants_dir

    return variants_dir() / "proposals"


def build_corpus(days: int, max_mails: int) -> dict:
    """Rassemble le corpus depuis la base. Pure lecture, sérialisable en JSON."""
    from ekoalu.email_canal.models import EmailLeadData
    from ekoalu.inbox_assist.models import PendingReply
    from ekoalu.inbox_assist.models import CorrectionExample
    from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

    since = timezone.now() - timedelta(days=days)
    sent = list(
        PendingOutbound.objects
        .filter(kind=OutboundKind.EMAIL_COLD, status=OutboundStatus.SENT, sent_at__gte=since)
        .order_by("-sent_at")[:max_mails]
    )
    pids = {po.prospect_public_id for po in sent}
    segments = {
        pid: {"source": src, "naf": naf, "dpt": dpt}
        for pid, src, naf, dpt in EmailLeadData.objects
        .filter(lead__public_identifier__in=pids)
        .values_list("lead__public_identifier", "source", "code_naf", "dpt")
    }
    replies = {}
    for r in PendingReply.objects.filter(channel=PendingReply.CHANNEL_EMAIL,
                                         prospect_public_id__in=pids):
        replies.setdefault(r.prospect_public_id, []).append(
            {"intent": r.intent, "extrait": (r.inbound_message or "")[:600]},
        )
    mails = [{
        "variante": po.prompt_variant or "?",
        "objet": po.subject,
        "corps": po.content_to_send,
        "segment": segments.get(po.prospect_public_id, {}),
        "reponses": replies.get(po.prospect_public_id, []),
    } for po in sent]
    corrections = [{
        "type": c.kind, "consigne": (c.instruction or "")[:400],
        "explication": (c.explanation or "")[:300], "diff": (c.diff_lines or [])[:12],
    } for c in CorrectionExample.objects.filter(channel="email_cold").order_by("-pk")[:400]]
    rejected = list(
        PendingOutbound.objects
        .filter(kind=OutboundKind.EMAIL_COLD, status=OutboundStatus.REJECTED)
        .exclude(rejection_reason="")
        .order_by("-created_at")
        .values_list("rejection_reason", flat=True)[:200]
    )
    return {"mails": mails, "corrections": corrections, "motifs_refus": rejected,
            "fenetre_jours": days}


def _user_message(corpus: dict, production_prompt: str) -> str:
    return (
        f"Variante en production (v2) :\n```prompt\n{production_prompt}\n```\n\n"
        f"Corpus ({len(corpus['mails'])} mails envoyés sur {corpus['fenetre_jours']} jours, "
        f"{len(corpus['corrections'])} corrections, {len(corpus['motifs_refus'])} motifs de refus) :\n"
        + json.dumps(corpus, ensure_ascii=False)
    )


def _extract_prompt(text: str) -> str | None:
    marker = "```prompt"
    i = text.find(marker)
    if i < 0:
        return None
    j = text.find("```", i + len(marker))
    return text[i + len(marker):j].strip() if j > 0 else None


class Command(BaseCommand):
    help = "Produit une proposition de variante v3 du prompt cold mail (Fable 5.1, 1 appel/semaine)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=120)
        parser.add_argument("--max-mails", type=int, default=600)
        parser.add_argument("--dry-run", action="store_true",
                            help="Construit le corpus et l'écrit sans appeler l'API.")

    def handle(self, *args, **opts):
        from ekoalu.email_generator.prompts import active_variants
        from ekoalu.email_generator.generator import _get_anthropic_client

        out_dir = _proposals_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d")
        corpus = build_corpus(opts["days"], opts["max_mails"])
        if not corpus["mails"]:
            raise CommandError("Aucun cold mail envoyé sur la fenêtre : rien à apprendre.")
        production = active_variants()["v2"][0]
        user_msg = _user_message(corpus, production)
        (out_dir / f"corpus_{stamp}.json").write_text(
            json.dumps(corpus, ensure_ascii=False, indent=1), encoding="utf-8",
        )
        self.stdout.write(f"Corpus : {len(corpus['mails'])} mails, {len(user_msg)//1000} k caractères")
        if opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry-run : corpus écrit, pas d'appel API."))
            return

        client = _get_anthropic_client()
        if client is None:
            raise CommandError("Pas de client Anthropic (ANTHROPIC_API_KEY ou SiteConfig).")
        model = os.environ.get("EKOALU_LEARNER_MODEL", LEARNER_MODEL)
        # Fable 5.1 : thinking toujours actif (ne pas passer le paramètre),
        # streaming pour tenir le long appel, effort high pour l'analyse.
        with client.messages.stream(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            output_config={"effort": "high"},
            messages=[{"role": "user", "content": user_msg}],
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason == "refusal":
            raise CommandError("Le modèle a refusé la requête (stop_reason=refusal).")
        text = "".join(b.text for b in resp.content if b.type == "text")
        md_path = out_dir / f"v3_{stamp}.md"
        md_path.write_text(text, encoding="utf-8")
        prompt_v3 = _extract_prompt(text)
        if prompt_v3 and "{signature_block}" in prompt_v3:
            (out_dir / f"v3_{stamp}.txt").write_text(prompt_v3, encoding="utf-8")
            status = "prompt v3 extrait"
        else:
            status = "prompt v3 NON extrait (marqueur absent) : relire le .md"
        usage = getattr(resp, "usage", None)
        tokens = f"in={getattr(usage, 'input_tokens', '?')} out={getattr(usage, 'output_tokens', '?')}"
        self.stdout.write(self.style.SUCCESS(f"{md_path} — {status} — {tokens}"))
        try:
            from ekoalu.notifications.hub_events import post_event
            post_event("prospection.learner", "info",
                       f"Learner hebdo cold mail : proposition v3 du {stamp} ({status})",
                       {"file": str(md_path), "mails": len(corpus["mails"]), "tokens": tokens,
                        "activation": "copier v3.txt dans data/prompt_variants + active.json"})
        except Exception:  # noqa: BLE001 — best-effort, ne bloque jamais la commande
            logger.warning("Événement hub non déposé (learner_weekly)")
