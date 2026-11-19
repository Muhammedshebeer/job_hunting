from django.core.mail.backends.base import BaseEmailBackend


class DisabledEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise RuntimeError('Email delivery is disabled on the hosted dashboard.')
