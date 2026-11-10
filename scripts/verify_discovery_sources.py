"""Verify known public employer boards without loading Django or secrets."""
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ENDPOINTS = [
    'https://boards-api.greenhouse.io/v1/boards/ruyaai/jobs?content=true',
    'https://boards-api.greenhouse.io/v1/boards/sphereit/jobs?content=true',
    'https://api.ashbyhq.com/posting-api/job-board/ziina',
    'https://api.ashbyhq.com/posting-api/job-board/algodriven',
    'https://boards-api.greenhouse.io/v1/boards/sparkland/jobs?content=true',
    'https://api.ashbyhq.com/posting-api/job-board/qlub',
    'https://api.ashbyhq.com/posting-api/job-board/asula',
    'https://api.ashbyhq.com/posting-api/job-board/mexdigital',
    'https://api.ashbyhq.com/posting-api/job-board/LeanTech',
    'https://api.ashbyhq.com/posting-api/job-board/infinitefield',
    'https://api.ashbyhq.com/posting-api/job-board/odewithanthropic',
]


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirect refused')


def verify(url):
    result = {'endpoint': url}
    try:
        request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'UAEJobAgent/phase2'})
        with build_opener(ProxyHandler({}), NoRedirects()).open(request, timeout=20) as response:
            result['http_status'] = response.status
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise ValueError('Response limit')
            payload = json.loads(body)
        rows = payload.get('jobs')
        if not isinstance(rows, list):
            raise ValueError('No jobs list')
        result['jobs'] = len(rows)
        result['with_publication_date_in_list'] = sum(
            bool(row.get('first_published') or row.get('publishedAt')) for row in rows
        )
        result['verified'] = True
    except Exception as exc:
        result['verified'] = False
        result['error_type'] = type(exc).__name__
        if isinstance(exc, HTTPError):
            result['http_status'] = exc.code
    return result


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(verify, ENDPOINTS):
            print(json.dumps(result))
