def review_counts(request):
    """Pending items for the admin "Review" link (walls + community places)."""
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_staff):
        return {}
    from community.models import PostPlace
    from .models import PENDING, PasswordResetRequest, WallSubmission
    return {"review_pending": WallSubmission.objects.filter(status=PENDING).count()
                              + PostPlace.objects.filter(kind="new", status=PENDING).count()
                              + PasswordResetRequest.objects.filter(done=False).count()}
