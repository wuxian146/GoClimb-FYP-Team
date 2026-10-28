"""Database records for wall scans.

The route engine (goclimb_routes) does the computer vision and keeps its working
files on disk. These models are the GoClimb website's own record of every
uploaded wall: who sent it, whether an admin approved it, the review state of
each route, plus a backup of the photo and routes (so nothing is lost when a
hosting service wipes its disk on redeploy).
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from climbing.models import Crag, Route   # the team's app is registered as "frontend_app", so import the classes

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
STATUS_CHOICES = [(PENDING, "Pending review"), (APPROVED, "Approved"), (REJECTED, "Rejected")]


class ArAppToken(models.Model):
    """Lets the GoClimbAR Unity app act as the logged-in website user.

    The website hands a token to the app inside the "Open in AR camera" link;
    the app sends it back as `Authorization: Token <key>` on every request.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="ar_app_tokens", on_delete=models.CASCADE)
    key = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    LIFETIME_DAYS = 30

    @classmethod
    def issue(cls, user):
        return cls.objects.create(user=user, key=secrets.token_hex(24))

    @property
    def expired(self):
        return self.created_at < timezone.now() - timezone.timedelta(days=self.LIFETIME_DAYS)

    def __str__(self):
        return f"AR app token for {self.user} ({self.created_at:%Y-%m-%d})"


class WallSubmission(models.Model):
    """One scanned wall, as uploaded from the app (Vuforia camera) or the website."""
    wall_id = models.CharField(max_length=36, unique=True)
    name = models.CharField(max_length=120, blank=True)
    venue = models.CharField(max_length=120, blank=True)
    mode = models.CharField(max_length=10, blank=True)               # indoor / outdoor
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    crag = models.ForeignKey(Crag, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="scanned_walls", help_text="Map location this wall belongs to")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="wall_submissions")
    source = models.CharField(max_length=10, default="web")          # app / web
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    hold_count = models.PositiveIntegerField(default=0)
    route_count = models.PositiveIntegerField(default=0)
    # Backup copy (survives a wiped disk; used to restore the engine's files)
    photo = models.BinaryField(null=True, blank=True, editable=False)
    photo_type = models.CharField(max_length=20, default="image/jpeg")
    doc = models.JSONField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name or self.wall_id} @ {self.venue} [{self.status}]"

    def visible_to(self, user):
        if self.status == APPROVED:
            return True
        return bool(user and user.is_authenticated and (user.is_staff or self.uploaded_by_id == user.id))


class RouteReview(models.Model):
    """Admin decision for one route on a wall (routes are identified by the engine's R1, R2...)."""
    wall = models.ForeignKey(WallSubmission, related_name="route_reviews", on_delete=models.CASCADE)
    route_id = models.CharField(max_length=12)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    edited_by_admin = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("wall", "route_id")]
        ordering = ["route_id"]

    def __str__(self):
        return f"{self.wall.wall_id}/{self.route_id} [{self.status}]"


class RouteWallLink(models.Model):
    """Option A: a route from the crag's route list (team data) shown on a real scanned wall.

    Links climbing.Route (e.g. "Plus One 4A") to one route (R1, R2...) on a WallSubmission,
    so "View on wall" and the AR cameras show real holds instead of a drawing.
    """
    route = models.OneToOneField(Route, related_name="wall_link", on_delete=models.CASCADE)
    wall = models.ForeignKey(WallSubmission, related_name="route_links", on_delete=models.CASCADE)
    wall_route_id = models.CharField(max_length=12)
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    linked_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.route} -> {self.wall.wall_id}/{self.wall_route_id}"
