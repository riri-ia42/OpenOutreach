from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ekoalu', '0017_leaddiscovery'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProfileReadDay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(unique=True)),
                ('count', models.PositiveIntegerField(default=0)),
                ('notified', models.BooleanField(default=False, help_text="Mail d'alerte cap atteint deja envoye pour ce jour")),
            ],
            options={
                'verbose_name': 'Lectures profil / jour',
                'verbose_name_plural': 'Lectures profil / jour',
            },
        ),
    ]
