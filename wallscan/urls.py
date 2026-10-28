from django.urls import path

from . import views

app_name = "wallscan"

urlpatterns = [
    # Climbers
    path("scan/", views.scan_page, name="scan"),                      # /ar/scan/  Wall Scan (login required)
    path("app/", views.app_page, name="app"),                         # /ar/app/   get / open the GoClimbAR app
    path("app-token/", views.app_token, name="app_token"),            # token handed to the Unity app
    # Admins
    path("review/", views.review_list, name="review_list"),
    path("review/locations/new/", views.add_location, name="add_location"),
    path("review/<str:wall_id>/", views.review_wall, name="review_wall"),
    path("review/<str:wall_id>/decision/", views.review_wall_decision, name="review_wall_decision"),
    path("review/<str:wall_id>/routes/<str:route_id>/decision/", views.review_route_decision,
         name="review_route_decision"),
    path("review/<str:wall_id>/routes/<str:route_id>/link/", views.review_route_link, name="review_route_link"),
    path("route/<int:route_pk>/", views.route_view, name="route_view"),             # "View on wall" (Option A)
    # Route-engine API shared with the Unity app (must stay last: it catches everything else)
    path("", views.ar_api, name="editor"),
    path("<path:subpath>", views.ar_api, name="api"),
]
