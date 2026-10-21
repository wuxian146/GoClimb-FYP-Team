from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('ar/', include('wallscan.urls')),
    path('', include('climbing.urls')),
]
