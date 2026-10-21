import json

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .engine import get_engine


def _engine():
    return get_engine(
        data_dir=str(settings.GOCLIMB_AR_DATA),
        unity_dir=str(settings.GOCLIMB_AR_UNITY_EXPORT),
        admin_token=settings.GOCLIMB_AR_ADMIN_TOKEN,
        admin_open=settings.DEBUG and not settings.GOCLIMB_AR_ADMIN_TOKEN,
        prefix="/ar",
    )


# The Unity app has no CSRF cookie, so the AR API is exempt. Edits to SAVED walls
# still need the X-Admin-Token header (set GOCLIMB_ADMIN_TOKEN on Render).
@csrf_exempt
def ar_api(request, subpath=""):
    code, body, ctype = _engine().handle(
        request.method, "/" + subpath, request.GET.dict(), request.body, request.headers)
    if not isinstance(body, (bytes, bytearray)):
        body, ctype = json.dumps(body).encode(), "application/json"
    resp = HttpResponse(body, status=code, content_type=ctype)
    resp["Cache-Control"] = "no-store"
    return resp


def scan_page(request):
    """Browser version of the scanner: take/upload a wall photo, see holds + routes drawn on it."""
    return render(request, "wallscan/scan.html")
