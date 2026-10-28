from django.urls import path

from . import views

app_name = "preferences"

urlpatterns = [
    path("profile/preferences/", views.preferences_page, name="page"),
    path("api/profile/preferences/", views.preferences_api, name="api"),
]
