from django.urls import path

from . import views

app_name = "wallscan"

urlpatterns = [
    path("scan/", views.scan_page, name="scan"),          # /ar/scan/  -> phone + desktop web scanner
    path("", views.ar_api, name="editor"),                # /ar/       -> admin wall editor (from GoClimbAR)
    path("<path:subpath>", views.ar_api, name="api"),     # /ar/api/... -> same API the Unity app uses
]
