"""Community extras on top of the team's forum (climbing.ForumPost):

- PostPlace   where a post was climbed (known crag, a scanned wall, or a NEW place that admins review)
- Friendship  friend requests between climbers
- Meetup      a booked climbing session with invited friends (+ calendar export)
"""
from django.conf import settings
from django.db import models

from climbing.models import Crag, ForumPost          # the team's app is registered as "frontend_app": import classes
from wallscan.models import WallSubmission

User = settings.AUTH_USER_MODEL

VERIFIED, PENDING, APPROVED, REJECTED = "verified", "pending", "approved", "rejected"
PLACE_STATUS = [(VERIFIED, "Known location"), (PENDING, "Pending admin review"),
                (APPROVED, "Approved by admin"), (REJECTED, "Rejected by admin")]


class PostPlace(models.Model):
    post = models.OneToOneField(ForumPost, related_name="place", on_delete=models.CASCADE)
    kind = models.CharField(max_length=10)                     # crag / wall / new
    crag = models.ForeignKey(Crag, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    wall = models.ForeignKey(WallSubmission, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=120)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=PLACE_STATUS, default=PENDING)
    review_note = models.CharField(max_length=300, blank=True)
    reviewed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def current_status(self):
        """A tagged scanned wall follows its own review; known crags are verified."""
        if self.kind == "wall" and self.wall_id:
            return self.wall.status
        return self.status

    def __str__(self):
        return f"{self.name} [{self.current_status()}]"


class Friendship(models.Model):
    PENDING, ACCEPTED, DECLINED = "pending", "accepted", "declined"
    from_user = models.ForeignKey(User, related_name="friend_requests_sent", on_delete=models.CASCADE)
    to_user = models.ForeignKey(User, related_name="friend_requests_received", on_delete=models.CASCADE)
    status = models.CharField(max_length=10, default=PENDING,
                              choices=[(PENDING, "Pending"), (ACCEPTED, "Friends"), (DECLINED, "Declined")])
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("from_user", "to_user")]

    def __str__(self):
        return f"{self.from_user} -> {self.to_user} [{self.status}]"


class Meetup(models.Model):
    """A planned climbing session ("book time together")."""
    organizer = models.ForeignKey(User, related_name="meetups_organized", on_delete=models.CASCADE)
    title = models.CharField(max_length=120)
    crag = models.ForeignKey(Crag, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    place_name = models.CharField(max_length=120, blank=True)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    note = models.TextField(blank=True)
    from_post = models.ForeignKey(ForumPost, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    cancelled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]

    def place_label(self):
        return self.crag.name if self.crag_id else (self.place_name or "To be decided")

    def __str__(self):
        return f"{self.title} @ {self.place_label()} {self.starts_at:%Y-%m-%d %H:%M}"


class MeetupInvite(models.Model):
    INVITED, GOING, DECLINED = "invited", "going", "declined"
    meetup = models.ForeignKey(Meetup, related_name="invites", on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name="meetup_invites", on_delete=models.CASCADE)
    status = models.CharField(max_length=10, default=INVITED,
                              choices=[(INVITED, "Invited"), (GOING, "Going"), (DECLINED, "Can't make it")])
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("meetup", "user")]
