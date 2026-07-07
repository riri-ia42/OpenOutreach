from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ekoalu', '0024_sending_claim_anti_double_envoi'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApifyUsageDay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(unique=True)),
                ('count', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Profils Apify / jour',
                'verbose_name_plural': 'Profils Apify / jour',
            },
        ),
    ]
