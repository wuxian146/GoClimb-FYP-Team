from django.urls import path

from . import views

app_name = "community"

urlpatterns = [
    path("community/", views.community_page, name="page"),
    path("community/meetups/<int:meetup_id>/calendar.ics", views.meetup_ics, name="meetup_ics"),
    path("community/review/places/", views.review_places, name="review_places"),
    # JSON API
    path("api/community/feed/", views.feed, name="feed"),
    path("api/community/posts/", views.create_post, name="create_post"),
    path("api/community/place-options/", views.place_options, name="place_options"),
    path("api/community/friends/", views.friends, name="friends"),
    path("api/community/friends/request/", views.friend_request, name="friend_request"),
    path("api/community/friends/<int:request_id>/respond/", views.friend_respond, name="friend_respond"),
    path("api/community/friends/<int:user_id>/remove/", views.friend_remove, name="friend_remove"),
    path("api/community/meetups/", views.meetups, name="meetups"),
    path("api/community/meetups/new/", views.create_meetup, name="create_meetup"),
    path("api/community/meetups/<int:meetup_id>/respond/", views.meetup_respond, name="meetup_respond"),
    path("api/community/meetups/<int:meetup_id>/cancel/", views.meetup_cancel, name="meetup_cancel"),
    path("api/community/places/<int:place_id>/decision/", views.place_decision, name="place_decision"),
]
