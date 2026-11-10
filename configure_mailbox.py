"""Interactive local setup; does not load .env or connect to the mailbox."""
import argparse
import getpass

from jobs.services.alert_mailbox import MailboxError, load_mailbox_config, secure_keyring


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='email_alerts.local.json')
    args = parser.parse_args()
    try:
        config = load_mailbox_config(args.config)
        keyring = secure_keyring()
        password = getpass.getpass('Gmail app password (hidden; never enter your normal password): ')
        if not password.strip():
            raise MailboxError('Empty password; nothing stored')
        keyring.set_password(config['credential_service'], config['username'], password.replace(' ', ''))
        print('Stored in Windows Credential Manager. No mailbox connection was made.')
    except MailboxError as exc:
        parser.exit(1, str(exc) + '\n')
    except Exception:
        parser.exit(1, 'Credential setup failed; private details suppressed.\n')


if __name__ == '__main__':
    main()
