from django.conf import settings


def deployment_mode(request):
    return {'dashboard_read_only': getattr(settings, 'DASHBOARD_READ_ONLY', False)}
