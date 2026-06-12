from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0014_deal_connected_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="profile_snapshot",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="lead",
            name="profile_snapshot_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
