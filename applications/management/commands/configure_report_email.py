from getpass import getpass
from django.core.management.base import BaseCommand, CommandError
from applications.email_backend import load_delivery_config
from jobs.services.alert_mailbox import secure_keyring


class Command(BaseCommand):
    help = 'Store an explicitly authorized SMTP App Password in Windows Credential Manager; never prints it'

    def handle(self, *args, **options):
        try:
            config = load_delivery_config()
            store = secure_keyring()
            password = getpass('SMTP App Password (hidden): ')
            if not password.strip():
                raise ValueError
            store.set_password(config['credential_service'], config['username'],
                password.replace(' ', '') if config['host'] == 'smtp.gmail.com' else password)
        except Exception:
            raise CommandError('SMTP credential setup failed. Check authorization, TLS settings and Windows Credential Manager. Private details suppressed.') from None
        self.stdout.write('SMTP credential stored securely. Run send_test_email to verify delivery.')
