from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ekoalu', '0018_profilereadday'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingreply',
            name='cold_variant',
            field=models.CharField(blank=True, db_index=True, help_text='Variante de prompt du cold mail auquel ce prospect répond (brique K : conversion A/B par variante)', max_length=32),
        ),
    ]
