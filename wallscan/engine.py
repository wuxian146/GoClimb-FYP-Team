"""Bridge between Django and the GoClimbAR route engine (goclimb_routes).

This is a port of goclimb_routes/server.py's request dispatch, with no HTTP
server of its own: Django receives the request and calls ArEngine.handle().
Every URL the Unity app and the admin editor used on port 8765 works the same
way under the /ar/ prefix, so the Unity app only needs its Server Url changed
to  https://<your-site>/ar  (no C# changes).

Pure Python (no Django imports) so it can be tested on its own.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import threading

from goclimb_routes.scan import ScanExpired, ScanService
from goclimb_routes.schema import API_VERSION
from goclimb_routes.store import DuplicateWallError, Store, WallNotFound

ID = r"([0-9a-fA-F-]{36})"
SCAN = r"([0-9a-f]{32})"
EDITOR_HTML = os.path.join(os.path.dirname(os.path.abspath(__import__("goclimb_routes").__file__)),
                           "web", "index.html")
MAX_BODY = 40_000_000


class Forbidden(Exception):
    pass


class ArEngine:
    def __init__(self, data_dir: str, unity_dir: str, admin_token: str = "", admin_open: bool = False,
                 prefix: str = "/ar"):
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(unity_dir, exist_ok=True)
        self.store = Store(data_dir)
        self.service = ScanService(self.store)
        self.unity_dir = unity_dir
        self.admin_token = admin_token
        self.admin_open = admin_open          # only when there is no token AND DEBUG is on
        self.prefix = prefix.rstrip("/")

    # ------------------------------------------------------------------ helpers
    def _admin(self, headers, is_admin=False):
        if is_admin:                          # a logged-in staff user, checked by Django
            return
        if self.admin_token:
            if not hmac.compare_digest(headers.get("X-Admin-Token", "") or "", self.admin_token):
                raise Forbidden()
        elif not self.admin_open:
            raise Forbidden()

    @staticmethod
    def _json(body: bytes) -> dict:
        if len(body) > MAX_BODY:
            raise ValueError("request too large")
        return json.loads(body or b"{}")

    def _editor_html(self) -> bytes:
        with open(EDITOR_HTML, "r", encoding="utf-8") as f:
            html = f.read()
        p = self.prefix
        for q in ("'", '"', "`"):                 # the editor uses absolute paths; move them under /ar
            html = html.replace(q + "/api/", q + p + "/api/").replace(q + "/photo/", q + p + "/photo/")
        return html.encode("utf-8")

    # ------------------------------------------------------------------ dispatch
    def handle(self, method: str, p: str, q: dict, body: bytes, headers, is_admin: bool = False) -> tuple:
        """Returns (status, body, content_type). body is a dict (JSON) or bytes."""
        store, service = self.store, self.service
        try:
            if method == "GET" and p in ("/", "/index.html"):
                return 200, self._editor_html(), "text/html; charset=utf-8"
            if method == "GET" and p == "/api/version":
                return 200, {"api_version": API_VERSION, "name": "goclimb",
                             "admin_token_required": bool(self.admin_token)}, None
            if method == "GET" and p == "/api/admin/check":
                self._admin(headers, is_admin)
                return 200, {"ok": True}, None
            if method == "GET" and p == "/api/walls":
                near = None
                if "lat" in q and "lon" in q:
                    near = (float(q["lat"]), float(q["lon"]), float(q.get("radius", 25)))
                return 200, store.search_walls(q.get("venue"), q.get("name"), q.get("mode"), near,
                                               needs_review=q.get("review") == "1", text=q.get("q")), None
            if method == "POST" and p == "/api/walls":
                b = self._json(body)
                try:
                    wid = store.create_wall(b.get("name", ""), b.get("venue", ""), b.get("mode", ""),
                                            image_bytes=base64.b64decode(b.get("image_b64", "")),
                                            lat=b.get("lat"), lon=b.get("lon"),
                                            vuforia_target=b.get("vuforia_target") or None,
                                            allow_duplicate=bool(b.get("allow_duplicate")),
                                            holds=b.get("holds"), source=b.get("source") or "web")
                except DuplicateWallError as e:
                    return 409, {"error": "duplicate", "existing": e.existing}, None
                rep = store.generate_routes_for(wid, int(b.get("max_routes", 6)))
                doc = store.export_doc(wid)
                doc["warnings"] = doc["warnings"] + rep.get("warnings", [])
                return 201, {"wall_id": wid, "created": True, "doc": doc}, None
            if method == "POST" and p == "/api/scan":
                return 200, service.scan(self._json(body)), None
            m = re.fullmatch(r"/api/scans/" + SCAN, p)
            if m and method == "DELETE":
                return 200, service.discard(m.group(1)), None
            m = re.fullmatch(r"/api/scans/" + SCAN + r"/(save|regenerate)", p)
            if m and method == "POST":
                sid, what = m.groups()
                b = self._json(body)
                if what == "save":
                    try:
                        return 200, service.save(sid, b), None
                    except DuplicateWallError as e:
                        return 409, {"error": "duplicate", "existing": e.existing}, None
                return 200, service.regenerate(sid, int(b.get("max_routes", 6)), bool(b.get("refresh_only"))), None
            m = re.fullmatch(r"/api/walls/" + ID + "/unity", p)
            if m and method == "GET":
                return 200, store.export_doc(m.group(1)), None
            m = re.fullmatch(r"/photo/" + ID, p)
            if m and method == "GET":
                path = store.photo_path(m.group(1))
                if path is None:
                    return 404, {"error": "photo missing"}, None
                with open(path, "rb") as f:
                    return 200, f.read(), "image/png" if path.endswith("png") else "image/jpeg"
            m = re.fullmatch(r"/overlay/" + ID, p)
            if m and method == "GET":
                store.save_overlay(m.group(1))
                with open(os.path.join(store.root, "walls", m.group(1), "overlay.png"), "rb") as f:
                    return 200, f.read(), "image/png"
            m = re.fullmatch(r"/api/walls/" + ID, p)
            if m and method == "GET":
                w = store.get_wall(m.group(1))
                w["detection_report"] = store.detection_report(m.group(1))
                return 200, w, None

            # -------------------------------------------------------------- admin (edits saved walls)
            if m and method in ("PUT", "DELETE"):
                self._admin(headers, is_admin)
                wid = m.group(1)
                if method == "PUT":
                    b = self._json(body)
                    store.update_wall(wid, allow_duplicate=bool(b.pop("allow_duplicate", False)), **b)
                    return 200, {"ok": True}, None
                store.delete_wall(wid, confirm=q.get("confirm") == "1")
                return 200, {"ok": True}, None
            m = re.fullmatch(r"/api/walls/" + ID + r"/(holds|redetect|routes/generate|routes|export|review)", p)
            if m and method == "POST":
                self._admin(headers, is_admin)
                wid, what = m.groups()
                b = self._json(body)
                if what == "holds":
                    return 201, {"hold_id": store.add_hold(wid, float(b["x"]), float(b["y"]))}, None
                if what == "redetect":
                    return 200, {"added": store.redetect(wid)}, None
                if what == "routes/generate":
                    return 200, store.generate_routes_for(
                        wid, int(b.get("max_routes", 6)), float(b.get("overlap_threshold", 0.6))), None
                if what == "routes":
                    return 201, {"route_id": store.add_route(wid, list(b.get("hold_ids") or []))}, None
                if what == "review":
                    store.set_reviewed(wid, bool(b.get("reviewed", True)))
                    return 200, {"ok": True}, None
                return 200, {"path": store.export_unity(wid, self.unity_dir)}, None
            m = re.fullmatch(r"/api/walls/" + ID + r"/holds/(H\d+)", p)
            if m and method in ("PUT", "DELETE"):
                self._admin(headers, is_admin)
                wid, hid = m.groups()
                if method == "PUT":
                    b = self._json(body)
                    store.move_hold(wid, hid, float(b["x"]), float(b["y"]))
                else:
                    store.delete_hold(wid, hid)
                return 200, {"ok": True}, None
            m = re.fullmatch(r"/api/walls/" + ID + r"/routes/(R\d+)", p)
            if m and method == "DELETE":
                self._admin(headers, is_admin)
                store.delete_route(*m.groups())
                return 200, {"ok": True}, None
            m = re.fullmatch(r"/api/walls/" + ID + r"/routes/(R\d+)/(limb|sequence)", p)
            if m and method == "POST":
                self._admin(headers, is_admin)
                wid, rid, what = m.groups()
                b = self._json(body)
                if what == "limb":
                    store.set_limb(wid, rid, b["hold_id"], b.get("limb") or None)
                else:
                    store.set_route_sequence(wid, rid, list(b["hold_ids"]))
                return 200, {"ok": True}, None
            return 404, {"error": "not found"}, None
        except Forbidden:
            return 401, {"error": "admin token required"}, None
        except ScanExpired as e:
            return 410, {"error": str(e)}, None
        except WallNotFound:
            return 404, {"error": "wall not found"}, None
        except KeyError as e:
            return 404, {"error": f"not found: {e}"}, None
        except (ValueError, TypeError, FileNotFoundError) as e:
            return 400, {"error": str(e)}, None
        except Exception as e:                      # never drop the connection silently
            return 500, {"error": f"internal error: {type(e).__name__}: {e}"}, None


_engine = None
_lock = threading.Lock()


def get_engine(**kwargs) -> ArEngine:
    """One engine per process. Scan sessions live in memory, so run gunicorn with ONE worker."""
    global _engine
    with _lock:
        if _engine is None:
            _engine = ArEngine(**kwargs)
        return _engine
