import json
import re

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST

from climbing.models import Crag, Route

from . import records
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


def _engine():
    return get_engine(
        data_dir=str(settings.GOCLIMB_AR_DATA),
        unity_dir=str(settings.GOCLIMB_AR_UNITY_EXPORT),
        admin_token=settings.GOCLIMB_AR_ADMIN_TOKEN,
        admin_open=False,                    # editing walls = logged-in staff (or the admin token)
        prefix="/ar",
    )


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
        if method in SAFE and not records.wall_visible(user, sub):
            return _json({"error": "wall not found"}, 404)

    engine = _engine()
    code, out, ctype = engine.handle(method, p, request.GET.dict(), body, request.headers, is_admin=is_staff)

    # Engine lost its files (e.g. hosting disk wiped)? Serve the database backup.
    if code == 404 and sub is not None and method in SAFE:
        if p.startswith("/photo/") and sub.photo:
            resp = HttpResponse(bytes(sub.photo), content_type=sub.photo_type)
            resp["Cache-Control"] = "no-store"
            return resp
        if p.endswith("/unity") and sub.doc:
            return _json(records.doc_for(user, sub, sub.doc))

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
    if sub is not None and isinstance(out, dict) and (p.endswith("/unity") or WALL_ONE.match(p)):
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
    return render(request, "wallscan/review_list.html", {"walls": walls[:200], "status": status, "counts": counts})


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
    return render(request, "wallscan/review_wall.html", {
        "wall": sub, "crags": crags, "link_data": {"crag_routes": crag_routes, "links": links,
                                                   "crag_name": sub.crag.name if sub.crag_id else ""}})


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
    return _json({"ok": True, "linked": route.pk})


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
# Admin: add a new location (crag) by clicking on the map
# =====================================================================

@ensure_csrf_cookie
def add_location(request):
    deny = _staff_or_redirect(request)
    if deny:
        return deny
    from climbing.views import CragManagementForm      # the team's existing crag form + validation
    form = CragManagementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        crag = form.save()
        return redirect(f"/ar/review/locations/new/?added={crag.pk}")
    added_id = request.GET.get("added")
    added = Crag.objects.filter(pk=added_id).first() if added_id and added_id.isdigit() else None
    crags = [{"name": c["name"], "lat": float(c["lat"]), "lng": float(c["lng"])}
             for c in Crag.objects.values("name", "lat", "lng")]
    return render(request, "wallscan/add_location.html", {"form": form, "added": added, "crags": crags})
