from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('ar/', include('wallscan.urls')),
    path('', include('pwa.urls')),        # /manifest.webmanifest, /sw.js, /offline/
    path('', include('preferences.urls')),  # /profile/preferences/
    path('', include('community.urls')),    # /community/ + /api/community/...
    path('', include('climbing.urls')),
]
