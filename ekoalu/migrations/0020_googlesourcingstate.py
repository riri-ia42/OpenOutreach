from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("ekoalu", "0019_pendingreply_cold_variant"),
        ("linkedin", "0007_siteconfig_llm_provider"),
    ]

    operations = [
        migrations.CreateModel(
            name="GoogleSourcingState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("consecutive_empty_runs", models.PositiveIntegerField(default=0)),
                ("exhausted", models.BooleanField(default=False)),
                ("total_new_leads", models.PositiveIntegerField(default=0)),
                ("total_queries", models.PositiveIntegerField(default=0)),
                ("campaign", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="google_sourcing_state", to="linkedin.campaign")),
            ],
            options={
                "verbose_name": "État sourcing Google (rotation ABM)",
                "verbose_name_plural": "États sourcing Google (rotation ABM)",
            },
        ),
    ]
