"""Read-only migration verification; exports no candidate or job contents."""
import json
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = 'Print table row counts and applied migration names for comparing database copies; never modifies data'

    def handle(self, *args, **options):
        existing = set(connection.introspection.table_names())
        counts = {}
        for model in apps.get_models(include_auto_created=True):
            table = model._meta.db_table
            if table in existing:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM ' + connection.ops.quote_name(table))
                    counts[table] = cursor.fetchone()[0]
        migrations = sorted(f'{app}.{name}' for app, name in MigrationRecorder(connection).applied_migrations())
        self.stdout.write(json.dumps({'tables': counts, 'migrations': migrations}, sort_keys=True, indent=2))
