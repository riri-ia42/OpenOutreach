from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0012_alter_deal_outcome'),
    ]

    operations = [
        migrations.AddField(
            model_name='lead',
            name='email_bounced_at',
            field=models.DateTimeField(blank=True, help_text='Date du hard bounce (NDR) détecté — adresse invalide, exclu des envois', null=True),
        ),
    ]
