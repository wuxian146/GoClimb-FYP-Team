import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from climbing.models import Crag, ForumLike, ForumPost
from wallscan import records as wall_records
from wallscan.models import WallSubmission

from . import calendar
from .models import (APPROVED, PENDING, REJECTED, VERIFIED, Friendship, Meetup, MeetupInvite,
                     PostPlace)

User = get_user_model()

STATUS_LABEL = {
    VERIFIED: "Known location", PENDING: "Pending admin review",
    APPROVED: "Approved by admin", REJECTED: "Rejected by admin",
}


class Bad(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def api(methods, login=True):
    """JSON endpoint: login check, JSON body, friendly errors."""
    def wrap(fn):
        @require_http_methods(methods)
        def inner(request, *args, **kwargs):
            if login and not request.user.is_authenticated:
                return JsonResponse({"error": "Please log in first.", "login_url": "/login/?next=/community/"}, status=401)
            try:
                body = json.loads(request.body or b"{}") if request.method in ("POST", "PUT", "PATCH") else {}
                if not isinstance(body, dict):
                    raise Bad("Invalid request.")
                return JsonResponse(fn(request, body, *args, **kwargs), safe=False)
            except Bad as exc:
                return JsonResponse({"error": str(exc)}, status=exc.status)
            except (ValueError, json.JSONDecodeError):
                return JsonResponse({"error": "Invalid request."}, status=400)
        return inner
    return wrap


# ====================================================================== helpers

def friends_of(user):
    rows = Friendship.objects.filter(Q(from_user=user) | Q(to_user=user), status=Friendship.ACCEPTED)
    return {r.to_user_id if r.from_user_id == user.id else r.from_user_id for r in rows}


def relation_map(user):
    """other user id -> "friend" / "sent" / "received" (pending requests)."""
    out = {}
    if not user.is_authenticated:
        return out
    for r in Friendship.objects.filter(Q(from_user=user) | Q(to_user=user)).exclude(status=Friendship.DECLINED):
        other = r.to_user_id if r.from_user_id == user.id else r.from_user_id
        if r.status == Friendship.ACCEPTED:
            out[other] = "friend"
        elif out.get(other) != "friend":
            out[other] = "sent" if r.from_user_id == user.id else "received"
    return out


def place_json(place):
    if place is None:
        return None
    status = place.current_status()
    data = {
        "id": place.pk, "kind": place.kind, "name": place.name,
        "lat": place.lat, "lng": place.lng,
        "status": status, "status_label": STATUS_LABEL.get(status, status),
        "note": place.review_note,
        "crag": {"id": place.crag_id, "name": place.crag.name, "slug": place.crag.slug} if place.crag_id else None,
        "wall_id": place.wall.wall_id if place.wall_id else None,
    }
    return data


def post_json(post, me, relations, liked):
    try:
        place = post.place
    except PostPlace.DoesNotExist:
        place = None
    if place is None and post.crag_id:                    # older posts tagged with a crag
        place_data = {"id": None, "kind": "crag", "name": post.crag.name,
                      "lat": float(post.crag.lat), "lng": float(post.crag.lng),
                      "status": VERIFIED, "status_label": STATUS_LABEL[VERIFIED], "note": "",
                      "crag": {"id": post.crag_id, "name": post.crag.name, "slug": post.crag.slug}, "wall_id": None}
    else:
        place_data = place_json(place)
    author_id = post.author_id
    return {
        "id": post.pk, "title": post.title, "content": post.content,
        "created_at": post.created_at.isoformat(),
        "author": {"id": author_id, "username": post.author.get_username()},
        "relation": "me" if me.is_authenticated and author_id == me.id else relations.get(author_id, "none"),
        "like_count": post.n_likes, "comment_count": post.n_comments, "is_liked": post.pk in liked,
        "place": place_data,
    }


def meetup_json(m, me, host=""):
    invites = list(m.invites.all())
    going = [m.organizer.get_username()] + [i.user.get_username() for i in invites if i.status == MeetupInvite.GOING]
    mine = next((i for i in invites if i.user_id == me.id), None)
    return {
        "id": m.pk, "title": m.title, "place": m.place_label(),
        "crag_slug": m.crag.slug if m.crag_id else None,
        "lat": float(m.crag.lat) if m.crag_id else m.lat, "lng": float(m.crag.lng) if m.crag_id else m.lng,
        "starts_at": m.starts_at.isoformat(), "ends_at": m.ends_at.isoformat(),
        "note": m.note, "cancelled": m.cancelled,
        "organizer": m.organizer.get_username(), "is_organizer": m.organizer_id == me.id,
        "my_status": "organizer" if m.organizer_id == me.id else (mine.status if mine else None),
        "people": [{"username": i.user.get_username(), "status": i.status} for i in invites],
        "going": going,
        "ics_url": f"/community/meetups/{m.pk}/calendar.ics",
        "google_url": calendar.google_link(m, going),
    }


def _float(v, lo, hi, label):
    if v in (None, ""):
        return None
    f = float(v)
    if not (lo <= f <= hi):
        raise Bad(f"{label} is out of range.")
    return f


# ====================================================================== pages

@ensure_csrf_cookie
def community_page(request):
    crags = list(Crag.objects.order_by("name").values("id", "name", "slug", "lat", "lng"))
    for c in crags:
        c["lat"], c["lng"] = float(c["lat"]), float(c["lng"])
    return render(request, "community/community.html", {"crags": crags})


# ====================================================================== posts

@api(["GET"], login=False)
def feed(request, body):
    qs = (ForumPost.objects.select_related("author", "crag", "place", "place__crag", "place__wall")
          .annotate(n_likes=Count("likes", distinct=True), n_comments=Count("comments", distinct=True))
          .order_by("-created_at"))
    crag = request.GET.get("crag")
    if crag:
        qs = qs.filter(Q(crag__slug=crag) | Q(place__crag__slug=crag))
    if request.GET.get("friends") == "1" and request.user.is_authenticated:
        qs = qs.filter(author_id__in=friends_of(request.user) | {request.user.id})
    posts = list(qs[:100])
    me = request.user
    liked = set(ForumLike.objects.filter(user=me, post__in=posts).values_list("post_id", flat=True)) if me.is_authenticated else set()
    rel = relation_map(me)
    return {"results": [post_json(p, me, rel, liked) for p in posts]}


@api(["POST"])
@transaction.atomic
def create_post(request, body):
    title = (body.get("title") or "").strip()[:160]
    content = (body.get("content") or "").strip()
    if not title or not content:
        raise Bad("Please write a title and a message.")
    place = body.get("place") or {}
    kind = place.get("kind") or ""
    post = ForumPost(author=request.user, title=title, content=content)

    if kind == "":
        post.save()
        place_obj = None
    elif kind == "crag":
        crag = Crag.objects.filter(pk=place.get("crag_id")).first()
        if crag is None:
            raise Bad("That location doesn't exist.")
        post.crag = crag
        post.save()
        place_obj = PostPlace.objects.create(post=post, kind="crag", crag=crag, name=crag.name,
                                             lat=float(crag.lat), lng=float(crag.lng), status=VERIFIED)
    elif kind == "wall":
        wall = WallSubmission.objects.filter(wall_id=place.get("wall_id")).first()
        if wall is None or not wall_records.wall_visible(request.user, wall):
            raise Bad("That scanned wall isn't available.")
        post.crag = wall.crag
        post.save()
        name = " · ".join(x for x in (wall.name, wall.venue) if x) or "Scanned wall"
        place_obj = PostPlace.objects.create(post=post, kind="wall", wall=wall, crag=wall.crag, name=name[:120],
                                             lat=wall.lat, lng=wall.lon, status=wall.status)
    elif kind == "new":
        name = (place.get("name") or "").strip()[:120]
        if not name:
            raise Bad("Please name the place.")
        lat = _float(place.get("lat"), -90, 90, "Latitude")
        lng = _float(place.get("lng"), -180, 180, "Longitude")
        existing = Crag.objects.filter(name__iexact=name).first()
        if existing:                                      # typed a place we already know: use it
            post.crag = existing
            post.save()
            place_obj = PostPlace.objects.create(post=post, kind="crag", crag=existing, name=existing.name,
                                                 lat=float(existing.lat), lng=float(existing.lng), status=VERIFIED)
        else:
            post.save()
            place_obj = PostPlace.objects.create(post=post, kind="new", name=name, lat=lat, lng=lng, status=PENDING)
    else:
        raise Bad("Unknown place type.")

    post.n_likes = post.n_comments = 0
    return {"post": post_json(post, request.user, {}, set()),
            "message": "Posted! The new place is waiting for admin review." if place_obj and place_obj.status == PENDING else "Posted!"}


@api(["GET"])
def place_options(request, body):
    """What a climber can tag: known crags + scanned walls they can see."""
    walls = [w for w in WallSubmission.objects.select_related("crag").defer("photo", "doc").order_by("-created_at")[:300]
             if wall_records.wall_visible(request.user, w)]
    return {
        "walls": [{"wall_id": w.wall_id, "name": w.name or "Scanned wall", "venue": w.venue, "status": w.status,
                   "status_label": STATUS_LABEL.get(w.status, w.status), "mine": w.uploaded_by_id == request.user.id}
                  for w in walls],
    }


# ====================================================================== friends

@api(["GET"])
def friends(request, body):
    me = request.user
    rows = Friendship.objects.filter(Q(from_user=me) | Q(to_user=me)).select_related("from_user", "to_user")
    out = {"friends": [], "incoming": [], "outgoing": []}
    for r in rows:
        other = r.to_user if r.from_user_id == me.id else r.from_user
        item = {"request_id": r.pk, "user_id": other.pk, "username": other.get_username()}
        if r.status == Friendship.ACCEPTED:
            out["friends"].append(item)
        elif r.status == Friendship.PENDING:
            out["incoming" if r.to_user_id == me.id else "outgoing"].append(item)
    for k in out:
        out[k].sort(key=lambda i: i["username"].lower())
    return out


@api(["POST"])
def friend_request(request, body):
    me = request.user
    target = None
    if body.get("user_id"):
        target = User.objects.filter(pk=body["user_id"], is_active=True).first()
    elif body.get("username"):
        target = User.objects.filter(username__iexact=str(body["username"]).strip(), is_active=True).first()
    if target is None:
        raise Bad("No climber with that name.", 404)
    if target.pk == me.pk:
        raise Bad("That's you!")
    back = Friendship.objects.filter(from_user=target, to_user=me).first()
    if back and back.status == Friendship.PENDING:          # they already asked: accept
        back.status, back.responded_at = Friendship.ACCEPTED, timezone.now()
        back.save()
        return {"status": "friend", "message": f"You and {target.get_username()} are now friends."}
    if back and back.status == Friendship.ACCEPTED:
        return {"status": "friend", "message": "You're already friends."}
    fr, created = Friendship.objects.get_or_create(from_user=me, to_user=target)
    if fr.status == Friendship.ACCEPTED:
        return {"status": "friend", "message": "You're already friends."}
    if not created:
        fr.status, fr.responded_at = Friendship.PENDING, None
        fr.save()
    return {"status": "sent", "message": f"Friend request sent to {target.get_username()}."}


@api(["POST"])
def friend_respond(request, body, request_id):
    fr = get_object_or_404(Friendship, pk=request_id, to_user=request.user, status=Friendship.PENDING)
    fr.status = Friendship.ACCEPTED if body.get("accept") else Friendship.DECLINED
    fr.responded_at = timezone.now()
    fr.save()
    return {"status": fr.status}


@api(["POST"])
def friend_remove(request, body, user_id):
    Friendship.objects.filter(Q(from_user=request.user, to_user_id=user_id) | Q(from_user_id=user_id, to_user=request.user)).delete()
    return {"status": "removed"}


# ====================================================================== meetups ("book time together")

def _my_meetups(user):
    return (Meetup.objects.filter(Q(organizer=user) | Q(invites__user=user)).distinct()
            .select_related("organizer", "crag").prefetch_related("invites__user"))


@api(["GET"])
def meetups(request, body):
    since = timezone.now() - timedelta(hours=12)
    rows = _my_meetups(request.user).filter(ends_at__gte=since).order_by("starts_at")
    return {"results": [meetup_json(m, request.user) for m in rows]}


@api(["POST"])
@transaction.atomic
def create_meetup(request, body):
    me = request.user
    starts = parse_datetime(str(body.get("starts_at") or ""))
    if starts is None:
        raise Bad("Choose a date and time.")
    if timezone.is_naive(starts):
        starts = timezone.make_aware(starts)
    if starts < timezone.now() - timedelta(minutes=5):
        raise Bad("That time is in the past.")
    minutes = int(body.get("duration_minutes") or 120)
    if not 15 <= minutes <= 24 * 60:
        raise Bad("Duration must be between 15 minutes and 24 hours.")
    friend_ids = friends_of(me)
    invite_ids = {int(i) for i in (body.get("invite_user_ids") or [])}
    if not invite_ids:
        raise Bad("Invite at least one friend.")
    if not invite_ids <= friend_ids:
        raise Bad("You can only invite climbers who are your friends.", 403)

    crag, place_name, lat, lng = None, "", None, None
    post = ForumPost.objects.filter(pk=body.get("post_id")).first() if body.get("post_id") else None
    if body.get("crag_id"):
        crag = Crag.objects.filter(pk=body["crag_id"]).first()
        if crag is None:
            raise Bad("That location doesn't exist.")
    else:
        place_name = (body.get("place_name") or "").strip()[:120]
        lat = _float(body.get("lat"), -90, 90, "Latitude")
        lng = _float(body.get("lng"), -180, 180, "Longitude")
        if not place_name:
            raise Bad("Choose where you'll climb.")

    m = Meetup.objects.create(
        organizer=me, title=(body.get("title") or "Climbing session").strip()[:120] or "Climbing session",
        crag=crag, place_name=place_name, lat=lat, lng=lng,
        starts_at=starts, ends_at=starts + timedelta(minutes=minutes),
        note=(body.get("note") or "").strip()[:1000], from_post=post)
    MeetupInvite.objects.bulk_create([MeetupInvite(meetup=m, user_id=uid) for uid in invite_ids])
    m = _my_meetups(me).get(pk=m.pk)
    return {"meetup": meetup_json(m, me), "message": "Climb booked! Your friends have been invited."}


@api(["POST"])
def meetup_respond(request, body, meetup_id):
    inv = get_object_or_404(MeetupInvite, meetup_id=meetup_id, user=request.user)
    inv.status = MeetupInvite.GOING if body.get("going") else MeetupInvite.DECLINED
    inv.responded_at = timezone.now()
    inv.save()
    return {"status": inv.status}


@api(["POST"])
def meetup_cancel(request, body, meetup_id):
    m = get_object_or_404(Meetup, pk=meetup_id, organizer=request.user)
    m.cancelled = True
    m.save()
    return {"status": "cancelled"}


@login_required(login_url="/login/")
def meetup_ics(request, meetup_id):
    m = get_object_or_404(_my_meetups(request.user), pk=meetup_id)
    data = meetup_json(m, request.user)
    resp = HttpResponse(calendar.ics(m, data["going"], request.get_host().split(":")[0]),
                        content_type="text/calendar; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="goclimb-climb-{m.pk}.ics"'
    return resp


# ====================================================================== admin: review new places

@ensure_csrf_cookie
def review_places(request):
    if not request.user.is_authenticated:
        return redirect("/login/?next=/community/review/places/")
    if not request.user.is_staff:
        return HttpResponse("Administrator access is required.", status=403)
    status = request.GET.get("status", PENDING)
    places = PostPlace.objects.filter(kind="new").select_related("post__author", "crag", "reviewed_by").order_by("-created_at")
    if status in (PENDING, APPROVED, REJECTED):
        places = places.filter(status=status)
    counts = {s: PostPlace.objects.filter(kind="new", status=s).count() for s in (PENDING, APPROVED, REJECTED)}
    return render(request, "community/review_places.html", {"places": places[:200], "status": status, "counts": counts})


@api(["POST"])
@transaction.atomic
def place_decision(request, body, place_id):
    if not request.user.is_staff:
        raise Bad("Administrator access is required.", 403)
    place = get_object_or_404(PostPlace, pk=place_id, kind="new")
    status = body.get("status")
    if status not in (PENDING, APPROVED, REJECTED):
        raise Bad("status must be pending, approved or rejected")
    place.status = status
    place.review_note = (body.get("note") or "").strip()[:300]
    place.reviewed_by, place.reviewed_at = request.user, timezone.now()
    message = f"Place {status}."
    if status == APPROVED and body.get("add_to_map"):
        if place.lat is None or place.lng is None:
            raise Bad("This place has no map position, so it can't be added to the map. Approve without adding.")
        crag = Crag.objects.filter(name__iexact=place.name).first()
        if crag is None:
            crag = Crag.objects.create(name=place.name, lat=round(place.lat, 6), lng=round(place.lng, 6),
                                       description=f"Added from a community post by {place.post.author.get_username()}.")
            message = f"Approved and added \"{crag.name}\" to the map."
        else:
            message = f"Approved and linked to the existing location \"{crag.name}\"."
        place.crag = crag
        ForumPost.objects.filter(pk=place.post_id).update(crag=crag)
    place.save()
    return {"status": place.status, "message": message}
