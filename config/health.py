from django.http import JsonResponse
from django.views.decorators.http import require_safe


@require_safe
def healthz(request):
    # Liveness only: no database, credentials, personal data or version details.
    response = JsonResponse({'status': 'ok'})
    response['Cache-Control'] = 'no-store'
    return response
