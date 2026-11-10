from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('jobs', '0001_initial')]
    operations = [
        migrations.AddField('job', 'requires_review', models.BooleanField(default=False)),
        migrations.AddField('job', 'review_reasons', models.JSONField(default=list, blank=True)),
        migrations.AddField('job', 'discovery_metadata', models.JSONField(default=dict, blank=True)),
    ]
