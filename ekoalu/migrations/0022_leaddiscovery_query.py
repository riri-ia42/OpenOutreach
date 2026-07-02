from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ekoalu", "0021_profilereadday_sources"),
    ]

    operations = [
        migrations.AddField(
            model_name="leaddiscovery",
            name="query",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
