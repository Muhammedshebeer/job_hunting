from django.apps import AppConfig
class ApplicationsConfig(AppConfig):
    default_auto_field='django.db.models.BigAutoField'
    name='applications'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in
        user_logged_in.disconnect(dispatch_uid='update_last_login')
        user_logged_in.connect(update_local_last_login, dispatch_uid='uae_update_local_last_login')


def update_local_last_login(sender, user, **kwargs):
    from django.conf import settings
    from django.contrib.auth.models import update_last_login
    if not getattr(settings, 'DASHBOARD_READ_ONLY', False):
        update_last_login(sender, user, **kwargs)
