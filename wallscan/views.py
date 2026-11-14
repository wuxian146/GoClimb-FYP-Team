import json
import re
import threading

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST

from climbing.models import Crag, Route

from . import records, routes_data
from .engine import get_engine
from .models import APPROVED, PENDING, REJECTED, RouteReview, RouteWallLink, WallSubmission
from .templatetags.wallscan_links import visible_link

ID = r"[0-9a-fA-F-]{36}"
SAFE = ("GET", "HEAD", "OPTIONS")
LOGIN_URL = "/login/?next=/ar/scan/"

# Holds come from the detector and are fixed. Admins may only connect existing holds.
HOLD_EDIT = re.compile(rf"^/api/walls/{ID}/(holds(/.*)?|redetect)$")
WALL_READ = re.compile(rf"^/(?:api/walls/({ID})(?:/unity)?|photo/({ID})|overlay/({ID}))$")
ROUTE_ADD = re.compile(rf"^/api/walls/({ID})/routes$")
ROUTE_EDIT = re.compile(rf"^/api/walls/({ID})/routes/(R\d+)(?:/sequence|/limb)?$")
ROUTE_REGEN = re.compile(rf"^/api/walls/({ID})/routes/generate$")
WALL_ONE = re.compile(rf"^/api/walls/({ID})$")
SCAN_SAVE = re.compile(r"^/api/scans/[0-9a-f]{32}/save$")


_restore_lock = threading.Lock()


def _engine():
    engine = get_engine(
        data_dir=str(settings.GOCLIMB_AR_DATA),
        unity_dir=str(settings.GOCLIMB_AR_UNITY_EXPORT),
        admin_token=settings.GOCLIMB_AR_ADMIN_TOKEN,
        admin_open=False,                    # editing walls = logged-in staff (or the admin token)
        prefix="/ar",
    )
    # First use after the server (re)started: put back walls the disk lost (free hosting wipes it).
    if not getattr(engine, "restored_from_db", False):
        with _restore_lock:
            if not getattr(engine, "restored_from_db", False):
                try:
                    records.restore_missing_walls(engine)
                finally:
                    engine.restored_from_db = True
    return engine


def _json(data, status=200):
    resp = JsonResponse(data, status=status, safe=False)
    resp["Cache-Control"] = "no-store"
    return resp


def _csrf_problem(request):
    """Website (session) requests that change data must carry the CSRF token.
    The Unity app uses a token header instead, which another website cannot send."""
    check = CsrfViewMiddleware(lambda req: None)
    check.process_request(request)
    return check.process_view(request, None, (), {})


# =====================================================================
# The route-engine API (/ar/api/..., /ar/photo/...) used by the website AND the Unity app
# =====================================================================

@csrf_exempt
def ar_api(request, subpath=""):
    p = "/" + subpath
    method = request.method
    body = request.body                       # read once (also needed before the CSRF check)
    user, how = records.ar_user(request)
    is_staff = bool(user and user.is_staff)

    if how == "bad-token":
        return _json({"error": "Your GoClimb sign-in on this phone has expired. "
                               "Open the AR camera from the GoClimb website again."}, 401)
    if how == "session" and method not in SAFE:
        problem = _csrf_problem(request)
        if problem is not None:
            return problem

    # Old GoClimbAR wall editor: admins only
    if p in ("/", "/index.html"):
        if not user:
            return redirect("/login/?next=/ar/")
        if not is_staff:
            return HttpResponse("Administrator access is required.", status=403)

    if HOLD_EDIT.match(p) and method not in SAFE:
        return _json({"error": "Holds come from the detector and can't be changed. "
                               "Build or fix routes by connecting the existing holds."}, 403)

    # Scanning, saving and uploading require a GoClimb account
    if method not in SAFE and not user:
        return _json({"error": "Please log in to GoClimb first. On the phone, open the AR camera "
                               "from the GoClimb website while logged in.", "login_url": LOGIN_URL}, 401)

    # Reading one wall: only if this user may see it
    m = WALL_READ.match(p)
    sub = None
    if m:
        wall_id = next(g for g in m.groups() if g)
        sub = WallSubmission.objects.filter(wall_id=wall_id).first()
        allowed = records.wall_visible if p.startswith("/photo/") else records.wall_openable
        if method in SAFE and not allowed(user, sub):
            return _json({"error": "wall not found"}, 404)

    engine = _engine()
    code, out, ctype = engine.handle(method, p, request.GET.dict(), body, request.headers, is_admin=is_staff)

    # Admin deletes a wall whose engine files are already gone (e.g. hosting disk wiped): remove the record too
    if code == 404 and sub is not None and method == "DELETE" and is_staff and WALL_ONE.match(p):
        sub.delete()
        return _json({"ok": True})

    # Engine lost its files (e.g. hosting disk wiped)? Serve the database backup.
    if code == 404 and sub is not None and method in SAFE:
        if p.startswith("/photo/") and sub.photo:
            resp = HttpResponse(bytes(sub.photo), content_type=sub.photo_type)
            resp["Cache-Control"] = "no-store"
            return resp
        if p.endswith("/unity") and sub.doc:
            return _json(routes_data.annotate_doc(user, sub, records.doc_for(user, sub, dict(sub.doc))))

    if code == 401 and not is_staff:
        return _json({"error": "Only GoClimb administrators can edit saved walls."}, 403)

    if code < 300 and isinstance(out, (dict, list)):
        out = _after(engine, user, how, method, p, out, sub)

    if not isinstance(out, (bytes, bytearray)):
        out, ctype = json.dumps(out).encode(), "application/json"
    resp = HttpResponse(out, status=code, content_type=ctype)
    resp["Cache-Control"] = "no-store"
    return resp


def _after(engine, user, how, method, p, out, sub):
    """Keep the database in step with the engine, and hide what this user may not see."""
    source = "app" if how == "token" else "web"

    # New wall uploaded (Unity offline upload) or a scan saved (website or app) -> pending review
    if method == "POST" and (p == "/api/walls" or SCAN_SAVE.match(p)) and isinstance(out, dict) and out.get("wall_id"):
        new = records.sync_wall(engine, out["wall_id"], user=user, source=source)
        if new is not None:
            out["review_status"] = new.status
            if out.get("doc"):
                out["doc"] = records.doc_for(user, new, out["doc"])
        return out

    # A scan that matched a saved wall returns that wall's routes: only show what this user may see
    if p == "/api/scan" and isinstance(out, dict) and out.get("status") == "known" and out.get("wall_id"):
        known = WallSubmission.objects.filter(wall_id=out["wall_id"]).first()
        out["doc"] = records.doc_for(user, known, out.get("doc"))
        if known is not None and not records.wall_openable(user, known):
            out["unavailable"] = True
            out["message"] = ("This wall isn't available (it was not approved or is waiting for review). "
                              "If it's a different wall, save it as a new wall.")
        return out

    if method not in SAFE:
        # Admin route editing (only by connecting existing holds; the engine checks hold ids too)
        m = ROUTE_ADD.match(p)
        if m and method == "POST":
            s = records.sync_wall(engine, m.group(1))
            if s and isinstance(out, dict) and out.get("route_id"):
                records.mark_route(s, out["route_id"], user, status=APPROVED, edited=True)
            return out
        m = ROUTE_EDIT.match(p)
        if m:
            s = records.sync_wall(engine, m.group(1))
            if s and method == "POST":
                records.mark_route(s, m.group(2), user, edited=True)
            return out
        m = ROUTE_REGEN.match(p)
        if m:
            records.sync_wall(engine, m.group(1))
            return out
        m = WALL_ONE.match(p)
        if m:
            if method == "DELETE":
                WallSubmission.objects.filter(wall_id=m.group(1)).delete()
            else:
                records.sync_wall(engine, m.group(1))
        return out

    # Reads: filter for this user
    if p == "/api/walls" and isinstance(out, list):
        return records.filter_wall_list(user, out)
    if sub is not None and isinstance(out, dict) and p.endswith("/unity"):
        return routes_data.annotate_doc(user, sub, records.doc_for(user, sub, out))
    if sub is not None and isinstance(out, dict) and WALL_ONE.match(p):
        return records.doc_for(user, sub, out)
    return out


# =====================================================================
# Pages for climbers
# =====================================================================

def _ar_app_context(request):
    prefs = getattr(request.user, "climber_preference", None) if request.user.is_authenticated else None
    env = getattr(prefs, "environment", "") if prefs else ""
    return {
        "ar_app_url": settings.GOCLIMB_AR_APP_URL,
        "ar_android_package": settings.GOCLIMB_AR_ANDROID_PACKAGE,
        "default_mode": "outdoor" if env == "outdoor" else "indoor",
    }


@login_required(login_url="/login/")
@ensure_csrf_cookie
def scan_page(request):
    """Wall Scan: camera / photo -> holds + routes, saved walls, open the Vuforia AR camera."""
    return render(request, "wallscan/scan.html", _ar_app_context(request))


@ensure_csrf_cookie
def app_page(request):
    """Shown when the AR app isn't installed (or on a computer): how to get / open GoClimbAR."""
    return render(request, "wallscan/app.html", _ar_app_context(request))


@require_POST
def app_token(request):
    """Gives the logged-in website user a token for the GoClimbAR app (sent inside the AR link)."""
    if not request.user.is_authenticated:
        return _json({"error": "Please log in first.", "login_url": LOGIN_URL}, 401)
    tok = records.token_for(request.user)
    return _json({"token": tok.key, "username": request.user.get_username()})


# =====================================================================
# Admin review
# =====================================================================

def _staff_or_redirect(request):
    if not request.user.is_authenticated:
        return redirect(f"/login/?next={request.path}")
    if not request.user.is_staff:
        return HttpResponse("Administrator access is required.", status=403)
    return None


@ensure_csrf_cookie
def review_list(request):
    deny = _staff_or_redirect(request)
    if deny:
        return deny
    records.import_unrecorded_walls(_engine())
    status = request.GET.get("status", PENDING)
    walls = WallSubmission.objects.select_related("uploaded_by", "crag").defer("photo", "doc")
    if status in (PENDING, APPROVED, REJECTED):
        walls = walls.filter(status=status)
    counts = {s: WallSubmission.objects.filter(status=s).count() for s in (PENDING, APPROVED, REJECTED)}
    # New places climbers tagged on community posts are reviewed here too (same queue)
    from climbing.models import GRADE_CHOICES, ROCK_TYPE_CHOICES
    from community.models import PostPlace
    places = PostPlace.objects.filter(kind="new").select_related("post__author", "crag", "reviewed_by").order_by("-created_at")
    if status in (PENDING, APPROVED, REJECTED):
        places = places.filter(status=status)
    place_counts = {s: PostPlace.objects.filter(kind="new", status=s).count() for s in (PENDING, APPROVED, REJECTED)}
    from .models import PasswordResetRequest
    resets = PasswordResetRequest.objects.filter(done=False).select_related("user")[:200]
    return render(request, "wallscan/review_list.html", {
        "walls": walls[:200], "status": status, "counts": counts, "places": places[:200], "place_counts": place_counts,
        "grades": GRADE_CHOICES, "rock_types": ROCK_TYPE_CHOICES, "resets": resets})


@require_POST
def password_reset_action(request, pk):
    """Admin handles a climber's password reset request: set a new password, or dismiss it."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return _json({"error": "Administrator access is required."}, 403)
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    from .models import PasswordResetRequest
    req = PasswordResetRequest.objects.select_related("user").filter(pk=pk, done=False).first()
    if req is None:
        return _json({"error": "This request was already handled."}, 404)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _json({"error": "Invalid request."}, 400)
    action = body.get("action")
    name = req.user.get_username()
    if action == "set_password":
        password = body.get("password") or ""
        try:
            validate_password(password, req.user)
        except ValidationError as exc:
            return _json({"error": " ".join(exc.messages)}, 400)
        req.user.set_password(password)                    # also signs the climber out everywhere
        req.user.save(update_fields=["password"])
        req.outcome = PasswordResetRequest.PASSWORD_SET
        message = f"Password for {name} updated. Give it to them privately; they can now log in with it."
    elif action == "dismiss":
        req.outcome = PasswordResetRequest.DISMISSED
        message = f"Request from {name} dismissed. Their password was not changed."
    else:
        return _json({"error": "Choose set_password or dismiss."}, 400)
    req.done, req.handled_by, req.handled_at = True, request.user, timezone.now()
    req.save(update_fields=["done", "outcome", "handled_by", "handled_at"])
    return _json({"ok": True, "message": message})


@ensure_csrf_cookie
def review_wall(request, wall_id):
    deny = _staff_or_redirect(request)
    if deny:
        return deny
    sub = WallSubmission.objects.filter(wall_id=wall_id).first() or records.sync_wall(_engine(), wall_id, source="import")
    if sub is None:
        return HttpResponse("Wall not found.", status=404)
    crags = Crag.objects.order_by("name").only("id", "name")
    crag_routes = [{"id": r.pk, "label": f"{r.name} ({r.grade})"} for r in Route.objects.filter(crag=sub.crag)] if sub.crag_id else []
    links = {l.wall_route_id: l.route_id for l in RouteWallLink.objects.filter(wall=sub)}
    from preferences.grades import GRADES
    from preferences.models import HOLD_TYPES
    info = {r.route_id: {"name": r.name, "grade": r.grade, "style": r.style, "hold_types": r.hold_types or []}
            for r in sub.route_reviews.all()}
    return render(request, "wallscan/review_wall.html", {
        "wall": sub, "crags": crags, "link_data": {"crag_routes": crag_routes, "links": links,
                                                   "crag_name": sub.crag.name if sub.crag_id else "",
                                                   "info": info, "grades": GRADES, "hold_types": HOLD_TYPES,
                                                   "styles": [["boulder", "Boulder"], ["sport", "Sport"],
                                                              ["toprope", "Top rope"], ["trad", "Trad"]]}})


def _staff_json(request):
    if not (request.user.is_authenticated and request.user.is_staff):
        return _json({"error": "Administrator access is required."}, 403)
    return None


@require_POST
def review_wall_decision(request, wall_id):
    deny = _staff_json(request)
    if deny:
        return deny
    sub = get_object_or_404(WallSubmission, wall_id=wall_id)
    data = json.loads(request.body or b"{}")
    status = data.get("status")
    if status not in (PENDING, APPROVED, REJECTED):
        return _json({"error": "status must be pending, approved or rejected"}, 400)
    sub.status = status
    sub.review_note = (data.get("note") or "")[:1000]
    sub.reviewed_by = request.user
    sub.reviewed_at = timezone.now()
    if "crag_id" in data:
        sub.crag = Crag.objects.filter(pk=data["crag_id"]).first() if data["crag_id"] else None
    sub.save()
    if status == APPROVED and data.get("approve_pending_routes"):
        sub.route_reviews.filter(status=PENDING).update(status=APPROVED, reviewed_by=request.user,
                                                         reviewed_at=timezone.now())
    return _json({"ok": True, "status": sub.status})


@require_POST
def review_route_decision(request, wall_id, route_id):
    deny = _staff_json(request)
    if deny:
        return deny
    sub = get_object_or_404(WallSubmission, wall_id=wall_id)
    status = json.loads(request.body or b"{}").get("status")
    if status not in (PENDING, APPROVED, REJECTED):
        return _json({"error": "status must be pending, approved or rejected"}, 400)
    if not RouteReview.objects.filter(wall=sub, route_id=route_id).exists():
        return _json({"error": "route not found"}, 404)
    records.mark_route(sub, route_id, request.user, status=status)
    return _json({"ok": True, "route_id": route_id, "status": status})


@require_POST
def review_route_link(request, wall_id, route_id):
    """Option A: link a scanned route (R1...) to a route in the crag's route list (or unlink)."""
    deny = _staff_json(request)
    if deny:
        return deny
    sub = get_object_or_404(WallSubmission, wall_id=wall_id)
    if not RouteReview.objects.filter(wall=sub, route_id=route_id).exists():
        return _json({"error": "route not found"}, 404)
    target = json.loads(request.body or b"{}").get("route_pk")
    if not target:
        RouteWallLink.objects.filter(wall=sub, wall_route_id=route_id).delete()
        return _json({"ok": True, "linked": None})
    route = Route.objects.filter(pk=target).first()
    if route is None:
        return _json({"error": "That crag route doesn't exist."}, 404)
    if sub.crag_id != route.crag_id:
        return _json({"error": "Link the wall to the same map location as the route first."}, 400)
    RouteWallLink.objects.filter(wall=sub, wall_route_id=route_id).exclude(route=route).delete()   # one crag route per scanned route
    RouteWallLink.objects.update_or_create(route=route, defaults={"wall": sub, "wall_route_id": route_id, "linked_by": request.user})
    rv = RouteReview.objects.get(wall=sub, route_id=route_id)
    rv.name = rv.name or route.name
    rv.grade = rv.grade or route.grade
    rv.style = rv.style or route.route_type
    rv.save(update_fields=["name", "grade", "style"])
    return _json({"ok": True, "linked": route.pk, "info": {"name": rv.name, "grade": rv.grade, "style": rv.style}})


@ensure_csrf_cookie
def route_view(request, route_pk):
    """"View on wall": a crag route drawn on its real scanned wall, plus phone (Vuforia) and browser AR."""
    route = get_object_or_404(Route.objects.select_related("crag"), pk=route_pk)
    link = visible_link(request.user, route)
    ctx = _ar_app_context(request)
    ctx.update({"route": route, "crag": route.crag, "link": link,
                "view_data": {"wall_id": link.wall.wall_id, "route_id": link.wall_route_id} if link else None})
    return render(request, "wallscan/route_view.html", ctx)


# =====================================================================
# Admin: locations (crags) - list, add on the map, edit grades, manage routes
# =====================================================================

def _location_form_class():
    from climbing.views import CragManagementForm      # the team's form; we add grade checks on top
    from preferences.grades import rank

    class LocationForm(CragManagementForm):
        def clean(self):
            data = super().clean()
            lo, hi = data.get("difficulty_min"), data.get("difficulty_max")
            if lo and hi and rank(lo) is not None and rank(hi) is not None and rank(lo) > rank(hi):
                self.add_error("difficulty_max", "The hardest grade can't be easier than the easiest grade.")
            return data
    return LocationForm


@ensure_csrf_cookie
def locations_list(request):
    deny = _staff_or_redirect(request)
    if deny:
        return deny
    from django.db.models import Count
    q = request.GET.get("q", "").strip()
    crags = Crag.objects.annotate(n_routes=Count("routes", distinct=True)).order_by("name")
    if q:
        crags = crags.filter(name__icontains=q)
    walls = {}
    for cid in WallSubmission.objects.filter(crag__isnull=False).values_list("crag_id", flat=True):
        walls[cid] = walls.get(cid, 0) + 1
    rows = [{"crag": c, "walls": walls.get(c.pk, 0)} for c in crags]
    return render(request, "wallscan/locations_list.html", {"rows": rows, "q": q})


@ensure_csrf_cookie
def location_edit(request, pk=None):
    """Add (pk=None) or edit a location: map position, grades, and its routes with their own grades."""
    deny = _staff_or_redirect(request)
    if deny:
        return deny
    from climbing.models import GRADE_CHOICES, ROUTE_TYPE_CHOICES
    from preferences.grades import rank
    crag = get_object_or_404(Crag, pk=pk) if pk else None
    LocationForm = _location_form_class()
    form = LocationForm(instance=crag)
    message = request.GET.get("msg", "")
    route_error = ""
    action = request.POST.get("action", "save") if request.method == "POST" else ""

    if action == "save":
        form = LocationForm(request.POST, instance=crag)
        if form.is_valid():
            crag = form.save()
            return redirect(f"/ar/review/locations/{crag.pk}/?msg=Saved")
    elif crag and action in ("add_route", "update_route"):
        grades, types = dict(GRADE_CHOICES), dict(ROUTE_TYPE_CHOICES)
        name = (request.POST.get("name") or "").strip()[:120]
        grade, rtype = request.POST.get("grade"), request.POST.get("route_type")
        try:
            length = int(request.POST.get("length_m") or 15)
        except ValueError:
            length = -1
        if not name or grade not in grades or rtype not in types or not 1 <= length <= 1000:
            route_error = "Route needs a name, a grade, a type and a length (1-1000 m)."
        else:
            fields = {"name": name, "grade": grade, "route_type": rtype, "length_m": length,
                      "description": (request.POST.get("description") or "").strip()[:500]}
            if action == "add_route":
                Route.objects.create(crag=crag, **fields)
                return redirect(f"/ar/review/locations/{crag.pk}/?msg=Route added")
            Route.objects.filter(pk=request.POST.get("route_id"), crag=crag).update(**fields)
            return redirect(f"/ar/review/locations/{crag.pk}/?msg=Route updated")
    elif crag and action == "delete_route":
        Route.objects.filter(pk=request.POST.get("route_id"), crag=crag).delete()
        return redirect(f"/ar/review/locations/{crag.pk}/?msg=Route deleted")
    elif crag and action == "range_from_routes":
        ranks = sorted((rank(g), g) for g in crag.routes.values_list("grade", flat=True) if rank(g) is not None)
        if ranks:
            crag.difficulty_min, crag.difficulty_max = ranks[0][1], ranks[-1][1]
            crag.save(update_fields=["difficulty_min", "difficulty_max", "updated_at"])
            return redirect(f"/ar/review/locations/{crag.pk}/?msg=Grade range set to {ranks[0][1]}-{ranks[-1][1]}")
        message = "Add routes first."

    crags = [{"name": c["name"], "lat": float(c["lat"]), "lng": float(c["lng"])}
             for c in Crag.objects.exclude(pk=getattr(crag, "pk", None)).values("name", "lat", "lng")]
    routes = list(crag.routes.all().order_by("grade", "name")) if crag else []
    return render(request, "wallscan/add_location.html", {
        "form": form, "crag": crag, "crags": crags, "routes": routes, "message": message, "route_error": route_error,
        "grades": GRADE_CHOICES, "route_types": ROUTE_TYPE_CHOICES})


def add_location(request):                                  # old URL, kept working
    return location_edit(request)


# =====================================================================
# Multiple routes per wall: route info (admin), all routes + recommendations (users)
# =====================================================================

ROUTE_STYLES = ("boulder", "sport", "toprope", "trad")


@require_POST
def review_route_info(request, wall_id, route_id):
    """Admin sets one scanned route's own name / grade / style / hold types."""
    from preferences.grades import normalize
    from preferences.models import HOLD_TYPES
    deny = _staff_json(request)
    if deny:
        return deny
    sub = get_object_or_404(WallSubmission, wall_id=wall_id)
    rv = RouteReview.objects.filter(wall=sub, route_id=route_id).first()
    if rv is None:
        return _json({"error": "route not found"}, 404)
    data = json.loads(request.body or b"{}")
    grade = normalize(data.get("grade")).rstrip("+")
    if data.get("grade") and not grade:
        return _json({"error": "Unknown grade. Use 4A ... 8B."}, 400)
    style = data.get("style") or ""
    if style and style not in ROUTE_STYLES:
        return _json({"error": "Unknown style."}, 400)
    valid_holds = {k for k, _ in HOLD_TYPES}
    rv.name = (data.get("name") or "").strip()[:80]
    rv.grade = grade
    rv.style = style
    rv.hold_types = [h for h in (data.get("hold_types") or []) if h in valid_holds]
    rv.save(update_fields=["name", "grade", "style", "hold_types"])
    return _json({"ok": True, "info": {"name": rv.name, "grade": rv.grade, "style": rv.style, "hold_types": rv.hold_types}})


def _wall_doc(sub):
    """Current routes document: the engine's live copy, or the database backup."""
    code, out, _ = _engine().handle("GET", f"/api/walls/{sub.wall_id}/unity", {}, b"", {})
    return out if code == 200 and isinstance(out, dict) else (dict(sub.doc) if sub.doc else None)


def wall_routes_api(request, wall_id):
    """GET: every approved route on this wall, ranked for the current climber (best + second best)."""
    user, how = records.ar_user(request)
    sub = WallSubmission.objects.filter(wall_id=wall_id).first()
    if sub is None or not records.wall_openable(user, sub):
        return _json({"error": "wall not found"}, 404)
    doc = _wall_doc(sub)
    if doc is None:
        return _json({"error": "This wall has no route data."}, 404)
    result = routes_data.routes_for_user(user, sub, doc)
    result.update({"wall_id": sub.wall_id, "name": sub.name, "venue": sub.venue, "status": sub.status,
                   "holds": doc.get("holds") or [], "logged_in": bool(user)})
    return _json(result)


@ensure_csrf_cookie
def wall_page(request, wall_id):
    """All routes on one wall: real photo, pick a route (recommended ones first), phone or browser AR."""
    sub = WallSubmission.objects.select_related("crag").filter(wall_id=wall_id).first()
    if sub is None or not records.wall_openable(request.user, sub):
        return HttpResponse("Wall not found.", status=404)
    ctx = _ar_app_context(request)
    ctx.update({"wall": sub, "page_data": {"wall_id": sub.wall_id, "route": request.GET.get("route", "")}})
    return render(request, "wallscan/wall_page.html", ctx)


@ensure_csrf_cookie
def crag_ar_page(request, slug):
    """Every approved AR wall of a crag, with the best two routes for this climber across all of them."""
    from preferences.profile_data import profile_for
    from preferences.recommend import recommend_routes
    crag = get_object_or_404(Crag, slug=slug)
    walls = [w for w in WallSubmission.objects.filter(crag=crag).defer("photo") if records.wall_openable(request.user, w)]
    all_rows, cards = [], []
    for w in walls:
        doc = _wall_doc(w)
        rows = routes_data.wall_route_rows(w, doc) if doc else []
        for r in rows:
            r["wall_id"], r["wall_name"] = w.wall_id, w.name or "Wall"
        all_rows += rows
        cards.append({"wall": w, "approved_routes": sum(1 for r in rows if r["approved"]), "total": len(rows)})
    picks = recommend_routes(all_rows, profile_for(request.user))
    best = [r for r in picks["routes"] if r["rec_rank"]]
    return render(request, "wallscan/crag_ar.html", {"crag": crag, "cards": cards, "best": best, "message": picks["message"],
                                                      "approved_total": sum(c["approved_routes"] for c in cards)})
