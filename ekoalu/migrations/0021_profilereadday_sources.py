from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ekoalu", "0020_googlesourcingstate"),
    ]

    operations = [
        migrations.AddField(
            model_name="profilereadday",
            name="sources",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
