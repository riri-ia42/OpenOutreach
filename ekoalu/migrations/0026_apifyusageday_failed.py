"""ApifyUsageDay.failed — tentatives en echec du jour (remboursees du plafond).

15/07 : l'actor HarvestAPI limite les comptes Apify Free a 20 runs. Du 10 au
15/07, 40 echecs/jour consommaient le plafond EKOALU_APIFY_DAILY_CAP pour rien
(le daemon croyait le plafond atteint et repliait sur Voyager toute la
journee) et la panne restait invisible du recap.
"""
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ekoalu", "0025_apifyusageday"),
    ]

    operations = [
        migrations.AddField(
            model_name="apifyusageday",
            name="failed",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
