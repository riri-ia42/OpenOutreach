"""Deal.connected_at — date d'acceptation de l'invitation (1er passage CONNECTED).

Backfill : pour les Deals déjà Connected/Completed, on reprend la date de
création de la PREMIÈRE task follow_up (le scheduler en crée une à l'entrée
en CONNECTED — preuve fiable + timestamp fidèle). Pas de fallback approximatif :
un Deal sans trace de follow_up reste à NULL.
"""
from django.db import migrations, models


def backfill_connected_at(apps, schema_editor):
    Deal = apps.get_model("crm", "Deal")
    Task = apps.get_model("linkedin", "Task")

    deals = (
        Deal.objects.filter(
            state__in=["Connected", "Completed"], connected_at__isnull=True,
        ).select_related("lead")
    )
    for deal in deals:
        first_follow_up = (
            Task.objects.filter(
                task_type="follow_up",
                payload__campaign_id=deal.campaign_id,
                payload__public_id=deal.lead.public_identifier,
            )
            .order_by("created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        if first_follow_up:
            deal.connected_at = first_follow_up
            deal.save(update_fields=["connected_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0013_lead_email_bounced_at"),
        ("linkedin", "0005_remove_task_error"),
    ]

    operations = [
        migrations.AddField(
            model_name="deal",
            name="connected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_connected_at, migrations.RunPython.noop),
    ]
