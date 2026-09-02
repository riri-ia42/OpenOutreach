"""Dump campagnes + criteres + historique mots-cles pour analyse Richard."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "linkedin.django_settings")
django.setup()

from django.db.models import Count, Min, Max  # noqa: E402

from crm.models import Lead  # noqa: E402
from crm.models.deal import Deal  # noqa: E402
from linkedin.models import Campaign, SearchKeyword  # noqa: E402

out: dict = {}

# 1) Campagnes + criteres
camps = []
for c in Campaign.objects.all().order_by("name"):
    deals = Deal.objects.filter(campaign=c)
    by_state = dict(
        deals.values_list("state").annotate(n=Count("id")).values_list("state", "n")
    )
    by_outcome = dict(
        deals.exclude(outcome="").values_list("outcome").annotate(n=Count("id")).values_list("outcome", "n")
    )
    kws = list(
        SearchKeyword.objects.filter(campaign=c).values("keyword", "used", "used_at")
    )
    camps.append({
        "name": c.name,
        "is_freemium": c.is_freemium,
        "objective": (c.campaign_objective or "").strip(),
        "product_docs": (c.product_docs or "").strip(),
        "seed_count": len(c.seed_public_ids or []),
        "n_keywords": len(kws),
        "keywords": kws,
        "deals_total": deals.count(),
        "deals_by_state": by_state,
        "deals_by_outcome": by_outcome,
    })
out["campaigns"] = camps

# 2) Stats globales leads / deals
out["leads_total"] = Lead.objects.count()
out["leads_disqualified"] = Lead.objects.filter(disqualified=True).count()
out["leads_with_email"] = Lead.objects.exclude(contact_email__isnull=True).exclude(contact_email="").count()
out["leads_bdd_prospect"] = Lead.objects.filter(contact_email_source="bdd_prospect").count()
out["deals_total"] = Deal.objects.count()
out["deals_by_state_global"] = dict(
    Deal.objects.values_list("state").annotate(n=Count("id")).values_list("state", "n")
)
out["deals_by_outcome_global"] = dict(
    Deal.objects.exclude(outcome="").values_list("outcome").annotate(n=Count("id")).values_list("outcome", "n")
)

# 3) Historique mots-cles global (avec dates)
kw_all = list(
    SearchKeyword.objects.select_related("campaign")
    .order_by("used_at", "id")
    .values("campaign__name", "keyword", "used", "used_at")
)
out["keywords_all"] = kw_all
out["keywords_total"] = len(kw_all)
out["keywords_used"] = sum(1 for k in kw_all if k["used"])
agg = SearchKeyword.objects.filter(used=True).aggregate(first=Min("used_at"), last=Max("used_at"))
out["keywords_used_first"] = str(agg["first"])
out["keywords_used_last"] = str(agg["last"])

with open(os.path.join("data", "_research_dump.json"), "w", encoding="utf-8") as fh:
    json.dump(out, fh, default=str, ensure_ascii=False, indent=1)
print("OK leads=%s deals=%s campaigns=%s kw=%s" % (
    out["leads_total"], out["deals_total"], len(out["campaigns"]), out["keywords_total"]))
