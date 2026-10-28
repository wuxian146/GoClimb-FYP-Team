from django.contrib import admin

from .models import Friendship, Meetup, MeetupInvite, PostPlace


@admin.register(PostPlace)
class PostPlaceAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "status", "post", "created_at")
    list_filter = ("kind", "status")


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ("from_user", "to_user", "status", "created_at")
    list_filter = ("status",)


class MeetupInviteInline(admin.TabularInline):
    model = MeetupInvite
    extra = 0


@admin.register(Meetup)
class MeetupAdmin(admin.ModelAdmin):
    list_display = ("title", "organizer", "starts_at", "cancelled")
    inlines = [MeetupInviteInline]
