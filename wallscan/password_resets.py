"""Password reset requests (user story #6).

GoClimb cannot send emails, so the reset page records a request that administrators
see in the Review queue. The page shows the same message whatever is typed, so it
never reveals which email addresses have an account.
"""
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import PasswordResetRequest


def record_request(email):
    """Record (or refresh) the open request for the active account with this email.
    Returns the request, or None when no active account matches."""
    email = (email or "").strip()
    if not email:
        return None
    user = get_user_model().objects.filter(email__iexact=email, is_active=True).order_by("pk").first()
    if user is None:
        return None
    req = PasswordResetRequest.objects.filter(user=user, done=False).first()
    if req is None:
        return PasswordResetRequest.objects.create(user=user, email=email)
    req.times_requested += 1                              # asking again: one entry, not many
    req.last_requested_at = timezone.now()
    req.email = email
    req.save(update_fields=["times_requested", "last_requested_at", "email"])
    return req


def open_count():
    return PasswordResetRequest.objects.filter(done=False).count()
