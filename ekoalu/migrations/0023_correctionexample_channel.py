# Migration boucle d'apprentissage (audit 07/07) :
# - CorrectionExample.channel (linkedin_dm / email_cold / email_reply)
# - Kind.REJECTION (refus avec motif exploitable)
# - Data : canal deduit des slugs historiques (follow_up/invitation → linkedin_dm,
#   email_cold → email_cold, email_reply_* → email_reply) + nettoyage des slugs
#   "canal" qui polluaient l'axe persona (follow_up / invitation / email_cold → "").
from __future__ import annotations

from django.db import migrations, models


_CHANNEL_SLUGS = ("follow_up", "invitation", "email_cold")


def fill_channel(apps, schema_editor):
    CorrectionExample = apps.get_model("ekoalu", "CorrectionExample")
    CorrectionExample.objects.filter(
        persona_slug__startswith="email_reply",
    ).update(channel="email_reply")
    CorrectionExample.objects.filter(persona_slug="email_cold").update(channel="email_cold")
    # Les slugs "canal" ne sont pas des personas : on les vide, le canal porte l'info.
    CorrectionExample.objects.filter(persona_slug__in=_CHANNEL_SLUGS).update(persona_slug="")
    # Tout le reste (personas dg_*, archi_*, bet_*, moe_*, manual, …) = DM LinkedIn
    # (valeur par defaut du champ, rien a faire).


def noop(apps, schema_editor):
    pass  # reversible sans restauration : le canal se re-deduit des slugs


class Migration(migrations.Migration):

    dependencies = [
        ("ekoalu", "0022_leaddiscovery_query"),
    ]

    operations = [
        migrations.AddField(
            model_name="correctionexample",
            name="channel",
            field=models.CharField(
                choices=[
                    ("linkedin_dm", "DM LinkedIn"),
                    ("email_cold", "Cold email"),
                    ("email_reply", "Réponse email"),
                ],
                db_index=True,
                default="linkedin_dm",
                help_text="Canal du message corrigé — la génération filtre TOUJOURS par canal",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="correctionexample",
            name="kind",
            field=models.CharField(
                choices=[
                    ("text_correction", "Correction texte"),
                    ("instruction_only", "Consigne seule"),
                    ("both", "Correction + consigne"),
                    ("rejection", "Refus avec motif"),
                ],
                db_index=True,
                default="text_correction",
                max_length=20,
            ),
        ),
        migrations.RunPython(fill_channel, noop),
    ]
