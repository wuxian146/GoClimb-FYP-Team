from django.urls import path

from . import views

app_name = "pwa"

# These must live at the site root: a service worker only controls pages
# at or below the path it is served from, so /sw.js covers the whole site.
urlpatterns = [
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("offline/", views.offline, name="offline"),
]
