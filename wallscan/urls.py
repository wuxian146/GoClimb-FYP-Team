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
    path("review/locations/", views.locations_list, name="locations"),
    path("review/locations/new/", views.location_edit, name="add_location"),
    path("review/locations/<int:pk>/", views.location_edit, name="location_edit"),
    path("review/password-resets/<int:pk>/", views.password_reset_action, name="password_reset_action"),
    path("review/<str:wall_id>/", views.review_wall, name="review_wall"),
    path("review/<str:wall_id>/decision/", views.review_wall_decision, name="review_wall_decision"),
    path("review/<str:wall_id>/routes/<str:route_id>/decision/", views.review_route_decision,
         name="review_route_decision"),
    path("review/<str:wall_id>/routes/<str:route_id>/link/", views.review_route_link, name="review_route_link"),
    path("route/<int:route_pk>/", views.route_view, name="route_view"),             # "View on wall" (Option A)
    path("review/<str:wall_id>/routes/<str:route_id>/info/", views.review_route_info, name="review_route_info"),
    path("wall/<str:wall_id>/", views.wall_page, name="wall_page"),                    # all routes on one wall
    path("walls/<str:wall_id>/routes/", views.wall_routes_api, name="wall_routes_api"),  # JSON: routes + picks
    path("crag/<slug:slug>/", views.crag_ar_page, name="crag_ar"),                     # all AR walls of a crag
    # Route-engine API shared with the Unity app (must stay last: it catches everything else)
    path("", views.ar_api, name="editor"),
    path("<path:subpath>", views.ar_api, name="api"),
]
