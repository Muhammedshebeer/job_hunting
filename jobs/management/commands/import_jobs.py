import csv
from datetime import datetime
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from jobs.models import Job
class Command(BaseCommand):
    help='Import jobs from an authorized CSV export (ISO-8601 posted_at required)'
    def add_arguments(self, parser): parser.add_argument('csv_path')
    def handle(self, *args, **options):
        count=0
        with open(options['csv_path'],newline='',encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                try:
                    posted= datetime.fromisoformat(row['posted_at'].replace('Z','+00:00'))
                    if timezone.is_naive(posted): raise ValueError('Timezone offset required')
                    url=row['url'].strip()
                    if not url.startswith(('https://','http://')): raise ValueError('Invalid URL')
                    Job.objects.update_or_create(url=url,defaults={
                        'source':row['source'], 'source_id':row.get('source_id',''),
                        'company':row['company'], 'title':row['title'],
                        'location':row['location'], 'posted_at':posted,
                        'description':row['description'],
                        'min_experience':int(row['min_experience']) if row.get('min_experience') else None,
                        'max_experience':int(row['max_experience']) if row.get('max_experience') else None,
                    })
                    count+=1
                except (KeyError, ValueError) as exc:
                    self.stderr.write(f'Skipped row: {exc}')
        self.stdout.write(self.style.SUCCESS(f'Imported/updated {count} jobs'))
