"""Glue between the route engine (files on disk) and the GoClimb database.

- who is calling (website session or the Unity app's token)
- copying a wall's details into WallSubmission / RouteReview after every change
- deciding what each user is allowed to see
"""
import logging

from django.utils import timezone

from .models import APPROVED, PENDING, ArAppToken, RouteReview, WallSubmission

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- who is calling

def ar_user(request):
    """Returns (user or None, how): how is "session", "token", "bad-token" or None."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Token "):
        key = auth[6:].strip()
        tok = ArAppToken.objects.select_related("user").filter(key=key).first() if key else None
        if tok and not tok.expired and tok.user.is_active:
            ArAppToken.objects.filter(pk=tok.pk).update(last_used_at=timezone.now())
            return tok.user, "token"
        return None, "bad-token"
    if request.user.is_authenticated:
        return request.user, "session"
    return None, None


def token_for(user):
    """Reuse a recent token (so links stay short-lived but we don't create one per click)."""
    fresh_after = timezone.now() - timezone.timedelta(days=7)
    tok = ArAppToken.objects.filter(user=user, created_at__gte=fresh_after).order_by("-created_at").first()
    return tok or ArAppToken.issue(user)


# ---------------------------------------------------------------- engine -> database

def sync_wall(engine, wall_id, user=None, source=None):
    """Copy the engine's current version of a wall (details, routes, photo) into the database.

    Creates the WallSubmission the first time (status: pending review).
    """
    store = engine.store
    try:
        wall = store.get_wall(wall_id)
        doc = store.export_doc(wall_id)
    except Exception as exc:                       # wall gone / unreadable: keep what we have
        log.warning("sync_wall(%s) skipped: %s", wall_id, exc)
        return WallSubmission.objects.filter(wall_id=wall_id).first()

    sub = WallSubmission.objects.filter(wall_id=wall_id).first()
    if sub is None:
        sub = WallSubmission(wall_id=wall_id, uploaded_by=user if (user and user.is_authenticated) else None,
                             source=source or "web", status=PENDING)
    sub.name = (wall.get("name") or doc.get("name") or "")[:120]
    sub.venue = (wall.get("venue") or doc.get("venue") or "")[:120]
    sub.mode = (wall.get("mode") or doc.get("mode") or "")[:10]
    sub.lat = wall.get("lat")
    sub.lon = wall.get("lon")
    sub.doc = doc
    sub.hold_count = len(doc.get("holds") or [])
    sub.route_count = len(doc.get("routes") or [])
    if not sub.photo:
        path = store.photo_path(wall_id)
        if path:
            with open(path, "rb") as f:
                sub.photo = f.read()
            sub.photo_type = "image/png" if path.endswith("png") else "image/jpeg"
    sub.save()

    route_ids = [r.get("id") for r in (doc.get("routes") or []) if r.get("id")]
    RouteReview.objects.filter(wall=sub).exclude(route_id__in=route_ids).delete()
    existing = set(RouteReview.objects.filter(wall=sub).values_list("route_id", flat=True))
    RouteReview.objects.bulk_create([RouteReview(wall=sub, route_id=rid) for rid in route_ids if rid not in existing])
    return sub


def import_unrecorded_walls(engine, limit=500):
    """Walls that exist in the engine but not in the database (e.g. scanned before this
    feature existed) get a pending record so admins can review them."""
    known = set(WallSubmission.objects.values_list("wall_id", flat=True))
    added = 0
    for item in engine.store.search_walls(limit=limit):
        if item["wall_id"] not in known:
            sync_wall(engine, item["wall_id"], source="import")
            added += 1
    return added


def mark_route(sub, route_id, user, status=None, edited=None):
    review, _ = RouteReview.objects.get_or_create(wall=sub, route_id=route_id)
    if status:
        review.status = status
        review.reviewed_by = user
        review.reviewed_at = timezone.now()
    if edited is not None:
        review.edited_by_admin = edited
    review.save()
    return review


# ---------------------------------------------------------------- who can see what

def can_manage(user, sub):
    return bool(user and user.is_authenticated and (user.is_staff or (sub and sub.uploaded_by_id == user.id)))


def wall_visible(user, sub):
    if sub is None:                                # no record yet: only admins see it
        return bool(user and user.is_staff)
    return sub.visible_to(user)


def doc_for(user, sub, doc):
    """Regular users only see approved routes of approved walls.
    The uploader and admins see everything, plus the review state."""
    if sub is None or not isinstance(doc, dict):
        return doc
    doc = dict(doc)
    reviews = {r.route_id: r for r in sub.route_reviews.all()}
    if can_manage(user, sub):
        doc["review"] = {
            "status": sub.status,
            "note": sub.review_note,
            "routes": {rid: {"status": r.status, "edited_by_admin": r.edited_by_admin} for rid, r in reviews.items()},
        }
        return doc
    doc["routes"] = [r for r in (doc.get("routes") or [])
                     if reviews.get(r.get("id")) is not None and reviews[r.get("id")].status == APPROVED]
    return doc


def filter_wall_list(user, items):
    subs = {s.wall_id: s for s in WallSubmission.objects.filter(wall_id__in=[i["wall_id"] for i in items])}
    out = []
    for item in items:
        sub = subs.get(item["wall_id"])
        if not wall_visible(user, sub):
            continue
        item = dict(item)
        item["review_status"] = sub.status if sub else "unreviewed"
        item["mine"] = bool(sub and user and user.is_authenticated and sub.uploaded_by_id == user.id)
        out.append(item)
    return out
