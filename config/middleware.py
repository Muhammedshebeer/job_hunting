from django.conf import settings
from django.http import HttpResponseForbidden, HttpResponseNotFound
from django.urls import resolve, Resolver404


class ReadOnlyDashboardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, 'DASHBOARD_READ_ONLY', False):
            request.get_host()  # Validate Host even on early responses and health checks.
            try:
                match = resolve(request.path_info)
            except Resolver404:
                match = None
            if (match and ('admin' in match.namespaces or match.url_name == 'application_evidence')
                    or request.path_info.startswith('/media/')):
                return HttpResponseNotFound('Not available on the hosted dashboard.')
            if request.method not in ('GET', 'HEAD', 'OPTIONS') and not (
                    request.method == 'POST' and match and match.url_name in ('login', 'logout')):
                response = HttpResponseForbidden('This dashboard is read-only. Use your local application for changes.')
                response['Cache-Control'] = 'no-store'
                return response
        return self.get_response(request)
