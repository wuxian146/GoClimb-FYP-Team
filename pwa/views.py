import json

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.templatetags.static import static

# Bump this when you change what gets precached or the caching rules.
# (The browser also re-installs the worker whenever sw.js changes at all.)
CACHE_VERSION = "v4"

ICONS = {
    "192": "pwa/icons/icon-192.png",
    "512": "pwa/icons/icon-512.png",
    "maskable": "pwa/icons/icon-maskable-512.png",
    "apple": "pwa/icons/apple-touch-icon.png",
}


def manifest(request):
    data = {
        "name": "GoClimb",
        "short_name": "GoClimb",
        "description": "Find crags, plan routes and scan climbing walls - also offline.",
        "id": "/",
        "start_url": "/search/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#0f172a",
        "theme_color": "#0f172a",
        "icons": [
            {"src": static(ICONS["192"]), "sizes": "192x192", "type": "image/png"},
            {"src": static(ICONS["512"]), "sizes": "512x512", "type": "image/png"},
            {"src": static(ICONS["maskable"]), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Wall Scan", "url": "/ar/scan/", "icons": [{"src": static(ICONS["192"]), "sizes": "192x192"}]},
            {"name": "Map", "url": "/map/", "icons": [{"src": static(ICONS["192"]), "sizes": "192x192"}]},
        ],
    }
    resp = JsonResponse(data)
    resp["Content-Type"] = "application/manifest+json"
    return resp


def service_worker(request):
    # "/" only redirects to /search/, so precache the real pages.
    precache = ["/search/", "/map/", "/ar/scan/", "/offline/", "/manifest.webmanifest"] + [static(p) for p in ICONS.values()]
    js = render_to_string("pwa/sw.js", {
        "version": CACHE_VERSION,
        "precache_json": json.dumps(precache),
    })
    resp = HttpResponse(js, content_type="application/javascript; charset=utf-8")
    resp["Cache-Control"] = "no-cache"          # always check for a new worker
    resp["Service-Worker-Allowed"] = "/"
    return resp


def offline(request):
    return render(request, "pwa/offline.html")
