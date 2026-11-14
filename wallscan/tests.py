"""End-to-end tests for scanned walls, multiple routes per wall, recommendations and admin tools.

Run:  python manage.py test wallscan preferences community
"""
import json
import shutil
import tempfile
import uuid
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from climbing.models import ClimberProfile, Crag, Route
from preferences.models import ClimberPreference

from .models import APPROVED, PENDING, RouteReview, RouteWallLink, WallSubmission


class IsolatedEngine:
    """Point the route engine at a temporary folder so tests never touch real scanned walls."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp()
        self._settings = override_settings(GOCLIMB_AR_DATA=Path(self._tmp) / "data",
                                           GOCLIMB_AR_UNITY_EXPORT=Path(self._tmp) / "unity")
        self._settings.enable()
        import wallscan.engine as engine
        self._saved_engine, engine._engine = engine._engine, None

    def tearDown(self):
        import wallscan.engine as engine
        engine._engine = self._saved_engine
        self._settings.disable()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()


def make_doc(wall_id, route_ids):
    holds = [{"id": f"H{i}", "x": round(0.08 * i, 3), "y": round(1 - 0.08 * i, 3)} for i in range(1, 11)]
    routes = [{"id": rid, "hold_ids": [f"H{(n % 8) + 1}", f"H{(n % 8) + 2}", f"H{(n % 8) + 3}"],
               "color": "#22C55E", "difficulty": "medium", "points": [], "steps": []}
              for n, rid in enumerate(route_ids)]
    return {"schema": 4, "wall_id": wall_id, "name": "Test wall", "holds": holds, "routes": routes}


def make_wall(routes, status=APPROVED, crag=None, uploader=None, mode="indoor"):
    """routes: list of (route_id, review_status, grade, style, hold_types)"""
    wid = str(uuid.uuid4())
    sub = WallSubmission.objects.create(wall_id=wid, name="Cave", venue="Gym", mode=mode, status=status, crag=crag,
                                        uploaded_by=uploader, doc=make_doc(wid, [r[0] for r in routes]),
                                        photo=b"\xff\xd8\xff\xe0fake", photo_type="image/jpeg")
    for rid, st, grade, style, holds in routes:
        RouteReview.objects.create(wall=sub, route_id=rid, status=st, grade=grade, style=style, hold_types=holds,
                                   name=f"Route {rid}")
    return sub


def climber(username, max_grade="", experience="", styles=(), holds=(), environment="indoor", staff=False):
    u = User.objects.create_user(username, password="pw-12345678", is_staff=staff)
    ClimberProfile.objects.create(user=u, experience_level=experience)
    ClimberPreference.objects.create(user=u, region="sg-east", environment=environment, max_grade=max_grade,
                                     styles=list(styles), hold_types=list(holds))
    return u


# ---------------------------------------------------------------------------------------------------------
class MultipleRoutesTests(IsolatedEngine, TestCase):
    """Issue 5: one wall = many independent routes; API returns all approved; picks are personal."""

    def setUp(self):
        super().setUp()
        self.wall = make_wall([
            ("R1", APPROVED, "4A", "toprope", ["jugs"]),
            ("R2", APPROVED, "6B", "boulder", ["crimps", "edges"]),
            ("R3", APPROVED, "5B", "boulder", ["slopers"]),
            ("R4", APPROVED, "7B", "sport", ["pinches"]),
            ("R5", PENDING, "5A", "boulder", ["jugs"]),          # not approved: must never reach climbers
            ("R6", APPROVED, "", "boulder", []),                 # no grade: listed, never recommended
        ])
        self.beginner = climber("beginner", max_grade="5B", styles=["bouldering"], holds=["slopers", "jugs"])
        self.strong = climber("strong", max_grade="6C", styles=["bouldering"], holds=["crimps"])
        self.sporty = climber("sporty", experience="master", styles=["sport"], holds=["pinches"], environment="both")

    def routes_for(self, user):
        self.client.force_login(user)
        r = self.client.get(f"/ar/walls/{self.wall.wall_id}/routes/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_api_returns_every_approved_route_and_hides_pending(self):
        data = self.routes_for(self.beginner)
        ids = {r["id"] for r in data["routes"]}
        self.assertEqual(ids, {"R1", "R2", "R3", "R4", "R6"})
        self.assertNotIn("R5", ids)
        self.assertEqual(data["pending"], [])                    # normal climbers don't see pending routes
        for r in data["routes"]:                                 # each route keeps its own data
            self.assertEqual(len(r["hold_ids"]), 3)
            self.assertIn("grade", r)

    def test_recommendations_follow_each_climbers_preferences(self):
        b, s, m = self.routes_for(self.beginner), self.routes_for(self.strong), self.routes_for(self.sporty)
        self.assertEqual(b["recommended"], ["R3", "R1"])
        self.assertEqual(s["recommended"][0], "R2")
        self.assertEqual(m["recommended"][0], "R4")
        self.assertNotEqual(b["recommended"], ["R1", "R2"])      # not database order
        self.assertNotIn("R6", b["recommended"] + s["recommended"] + m["recommended"])
        best = next(r for r in b["routes"] if r["id"] == "R3")
        self.assertEqual(best["rec_rank"], 1)
        self.assertTrue(best["reasons"])                         # explains why

    def test_too_hard_routes_are_not_recommended_to_beginners(self):
        b = self.routes_for(self.beginner)
        hard = {r["id"]: r for r in b["routes"]}
        self.assertFalse({"R2", "R4"} & set(b["recommended"]))
        self.assertIn("above your level", hard["R4"]["unsuitable_reason"])

    def test_nothing_suitable_gives_a_clear_message(self):
        wall = make_wall([("R1", APPROVED, "8A", "sport", [])])
        self.client.force_login(self.beginner)
        data = self.client.get(f"/ar/walls/{wall.wall_id}/routes/").json()
        self.assertEqual(data["recommended"], [])
        self.assertIn("None of the approved routes suit", data["message"])

    def test_unity_document_has_names_grades_and_picks_best_first(self):
        self.client.force_login(self.strong)
        doc = self.client.get(f"/ar/api/walls/{self.wall.wall_id}/unity").json()
        ids = [r["id"] for r in doc["routes"]]
        self.assertNotIn("R5", ids)
        self.assertEqual(ids[0], "R2")                           # best match first
        first = doc["routes"][0]
        self.assertEqual((first["grade"], first["rec_rank"], first["name"]), ("6B", 1, "Route R2"))
        self.assertTrue(first["rec_reason"])
        self.assertIn("recommendation", doc)

    def test_uploader_sees_pending_routes_separately(self):
        owner = climber("owner", max_grade="6A")
        wall = make_wall([("R1", APPROVED, "5A", "boulder", []), ("R2", PENDING, "5B", "boulder", [])],
                         status=PENDING, uploader=owner)
        self.client.force_login(owner)
        data = self.client.get(f"/ar/walls/{wall.wall_id}/routes/").json()
        self.assertEqual(data["recommended"], [])                # wall itself not approved yet
        self.assertIn("waiting for admin review", data["message"])
        self.assertEqual({r["id"] for r in data["pending"]}, {"R1", "R2"})

    def test_strangers_cannot_open_a_pending_wall(self):
        wall = make_wall([("R1", APPROVED, "5A", "boulder", [])], status=PENDING, uploader=self.strong)
        self.client.force_login(self.beginner)
        self.assertEqual(self.client.get(f"/ar/walls/{wall.wall_id}/routes/").status_code, 404)

    def test_wall_and_crag_pages_render(self):
        crag = Crag.objects.create(name="Gym East", difficulty_min="4A", difficulty_max="7B")
        self.wall.crag = crag
        self.wall.save()
        self.client.force_login(self.beginner)
        self.assertContains(self.client.get(f"/ar/wall/{self.wall.wall_id}/"), "Routes on this wall")
        page = self.client.get(f"/ar/crag/{crag.slug}/")
        self.assertContains(page, "Best match")
        self.assertContains(page, "Route R3")


# ---------------------------------------------------------------------------------------------------------
class AdminRouteInfoTests(IsolatedEngine, TestCase):
    def setUp(self):
        super().setUp()
        self.admin = climber("admin", staff=True)
        self.user = climber("user")
        self.wall = make_wall([("R1", PENDING, "", "", []), ("R2", PENDING, "", "", [])], status=PENDING)

    def post(self, url, body):
        return self.client.post(url, json.dumps(body), content_type="application/json")

    def test_admin_sets_each_routes_own_grade_and_approves_several(self):
        self.client.force_login(self.admin)
        for rid, grade in (("R1", "5a"), ("R2", "6C")):
            r = self.post(f"/ar/review/{self.wall.wall_id}/routes/{rid}/info/",
                          {"name": f"Line {rid}", "grade": grade, "style": "boulder", "hold_types": ["crimps", "bogus"]})
            self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.post(f"/ar/review/{self.wall.wall_id}/decision/",
                                   {"status": "approved", "approve_pending_routes": True}).status_code, 200)
        rows = {rv.route_id: rv for rv in RouteReview.objects.filter(wall=self.wall)}
        self.assertEqual((rows["R1"].grade, rows["R2"].grade), ("5A", "6C"))     # "5a" normalised
        self.assertEqual(rows["R1"].hold_types, ["crimps"])                      # unknown hold type dropped
        self.assertTrue(all(rv.status == APPROVED for rv in rows.values()))
        self.client.force_login(self.user)
        data = self.client.get(f"/ar/walls/{self.wall.wall_id}/routes/").json()
        self.assertEqual({r["id"] for r in data["routes"]}, {"R1", "R2"})

    def test_bad_grade_and_non_admin_are_rejected(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.post(f"/ar/review/{self.wall.wall_id}/routes/R1/info/", {"grade": "V5"}).status_code, 400)
        self.client.force_login(self.user)
        self.assertEqual(self.post(f"/ar/review/{self.wall.wall_id}/routes/R1/info/", {"grade": "5A"}).status_code, 403)

    def test_linking_a_crag_route_fills_in_its_info(self):
        crag = Crag.objects.create(name="Linked Crag")
        route = Route.objects.create(crag=crag, name="Plus One", grade="4A", route_type="boulder")
        self.wall.crag = crag
        self.wall.save()
        RouteReview.objects.filter(wall=self.wall, route_id="R2").update(name="", grade="", style="")   # no info yet
        RouteReview.objects.filter(wall=self.wall, route_id="R1").update(name="Admin Name", grade="6A")  # admin already set
        self.client.force_login(self.admin)
        r = self.post(f"/ar/review/{self.wall.wall_id}/routes/R2/link/", {"route_pk": route.pk})
        self.assertEqual(r.status_code, 200, r.content)
        rv = RouteReview.objects.get(wall=self.wall, route_id="R2")
        self.assertEqual((rv.name, rv.grade, rv.style), ("Plus One", "4A", "boulder"))       # empty fields filled in
        self.assertTrue(RouteWallLink.objects.filter(route=route, wall=self.wall, wall_route_id="R2").exists())
        # linking never overwrites information an admin already entered
        other = Route.objects.create(crag=crag, name="Other", grade="4B", route_type="sport")
        self.assertEqual(self.post(f"/ar/review/{self.wall.wall_id}/routes/R1/link/", {"route_pk": other.pk}).status_code, 200)
        rv1 = RouteReview.objects.get(wall=self.wall, route_id="R1")
        self.assertEqual((rv1.name, rv1.grade, rv1.style), ("Admin Name", "6A", "sport"))


# ---------------------------------------------------------------------------------------------------------
class AdminLocationTests(IsolatedEngine, TestCase):
    """Issue 1: admins create and edit crags with grades, and manage the crag's routes."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user("admin", password="pw-12345678", is_staff=True)
        self.client.force_login(self.admin)

    def form(self, **kw):
        data = {"action": "save", "name": "New Crag", "slug": "", "description": "", "lat": "1.350000", "lng": "103.900000",
                "rock_type": "granite", "difficulty_min": "5A", "difficulty_max": "7A", "length_m": "20", "hazards": "", "image": ""}
        data.update(kw)
        return data

    def test_create_crag_with_grades(self):
        r = self.client.post("/ar/review/locations/new/", self.form())
        crag = Crag.objects.get(name="New Crag")
        self.assertRedirects(r, f"/ar/review/locations/{crag.pk}/?msg=Saved", fetch_redirect_response=False)
        self.assertEqual((crag.difficulty_min, crag.difficulty_max), ("5A", "7A"))
        self.assertContains(self.client.get(f"/ar/review/locations/{crag.pk}/"), "Routes at New Crag")

    def test_inverted_grade_range_is_rejected(self):
        r = self.client.post("/ar/review/locations/new/", self.form(difficulty_min="7A", difficulty_max="5A"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "hardest grade can")
        self.assertFalse(Crag.objects.filter(name="New Crag").exists())

    def test_edit_grades_and_manage_routes(self):
        crag = Crag.objects.create(name="Edit Me", difficulty_min="4A", difficulty_max="6B")
        self.client.post(f"/ar/review/locations/{crag.pk}/", self.form(name="Edit Me", difficulty_min="5B", difficulty_max="6C"))
        crag.refresh_from_db()
        self.assertEqual((crag.difficulty_min, crag.difficulty_max), ("5B", "6C"))
        url = f"/ar/review/locations/{crag.pk}/"
        for name, grade in (("Easy One", "4B"), ("Hard One", "7B")):
            self.client.post(url, {"action": "add_route", "name": name, "grade": grade, "route_type": "sport", "length_m": "12"})
        self.assertEqual(crag.routes.count(), 2)
        self.client.post(url, {"action": "range_from_routes"})
        crag.refresh_from_db()
        self.assertEqual((crag.difficulty_min, crag.difficulty_max), ("4B", "7B"))
        hard = crag.routes.get(name="Hard One")
        self.client.post(url, {"action": "update_route", "route_id": hard.pk, "name": "Hard One", "grade": "7C",
                               "route_type": "boulder", "length_m": "5"})
        hard.refresh_from_db()
        self.assertEqual((hard.grade, hard.route_type), ("7C", "boulder"))
        self.client.post(url, {"action": "delete_route", "route_id": hard.pk})
        self.assertEqual(crag.routes.count(), 1)

    def test_team_manage_page_also_rejects_inverted_range(self):
        """Needs apply_fixes.py (step 3). Uses the team's own /manage/ page."""
        r = self.client.post("/manage/crags/new/", {k: v for k, v in self.form(difficulty_min="7A", difficulty_max="5A").items() if k != "action"})
        self.assertFalse(Crag.objects.filter(name="New Crag").exists(),
                         "Run 'python apply_fixes.py': the team's crag form still accepts an inverted grade range.")
        r = self.client.post("/manage/crags/new/", {k: v for k, v in self.form().items() if k != "action"})
        self.assertEqual(Crag.objects.get(name="New Crag").difficulty_max, "7A")

    def test_non_admin_cannot_manage_locations(self):
        self.client.force_login(User.objects.create_user("normal", password="pw-12345678"))
        self.assertEqual(self.client.get("/ar/review/locations/").status_code, 403)
        self.client.post("/ar/review/locations/new/", self.form())
        self.assertFalse(Crag.objects.filter(name="New Crag").exists())


class RestoreAfterWipedDiskTests(IsolatedEngine, TestCase):
    """Free hosting wipes the disk on restart: walls must come back from the database with the same IDs."""

    def setUp(self):
        super().setUp()
        import cv2
        import numpy as np
        img = np.full((300, 400, 3), 200, np.uint8)
        cv2.circle(img, (100, 100), 20, (30, 30, 200), -1)
        self.photo = cv2.imencode(".jpg", img)[1].tobytes()
        self.wid = str(uuid.uuid4())
        doc = {"wall_id": self.wid, "name": "Backup wall", "venue": "Gym", "mode": "indoor", "updated_at": "2026-11-01T10:00:00",
               "holds": [{"id": f"H{i}", "x": 0.1 * i, "y": 0.9 - 0.1 * i, "confidence": 0.9, "type": "detected", "status": "detected"}
                         for i in range(1, 7)],
               "routes": [{"id": "R2", "hold_ids": ["H1", "H2", "H3"], "edited": False,
                           "points": [{"hold_id": "H1", "limb": "LH", "limb_status": "confirmed"}]},
                          {"id": "R5", "hold_ids": ["H4", "H5", "H6"], "edited": True, "points": []}]}
        self.sub = WallSubmission.objects.create(wall_id=self.wid, name="Backup wall", venue="Gym", mode="indoor",
                                                 status=APPROVED, doc=doc, photo=self.photo)
        for rid in ("R2", "R5"):
            RouteReview.objects.create(wall=self.sub, route_id=rid, status=APPROVED, grade="5A")
        self.admin = User.objects.create_user("admin", password="pw-12345678", is_staff=True)

    def test_first_request_restores_wall_with_same_ids(self):
        from wallscan.views import _engine
        engine = _engine()                                   # empty disk -> restored from the database
        doc = engine.store.export_doc(self.wid)
        self.assertEqual([h["id"] for h in doc["holds"]], [f"H{i}" for i in range(1, 7)])
        self.assertEqual([(r["id"], r["hold_ids"]) for r in doc["routes"]],
                         [("R2", ["H1", "H2", "H3"]), ("R5", ["H4", "H5", "H6"])])
        self.assertEqual(doc["routes"][0]["points"][0]["limb"], "LH")
        # admin editing works on the restored wall and new route numbers don't collide
        self.client.force_login(self.admin)
        r = self.client.post(f"/ar/api/walls/{self.wid}/routes", json.dumps({"hold_ids": ["H1", "H4"]}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["route_id"], "R6")
        self.assertEqual(self.client.get(f"/ar/photo/{self.wid}").content, self.photo)

    def test_manual_command(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("restore_walls", stdout=out)
        self.assertIn("Engine has 1 wall(s)", out.getvalue())


class RejectedWallTests(IsolatedEngine, TestCase):
    """Option B: a rejected wall is listed (with its status and note) for its uploader, but only admins can open it."""

    def setUp(self):
        super().setUp()
        self.owner = climber("owner")
        self.other = climber("other")
        self.admin = climber("admin", staff=True)
        self.wall = make_wall([("R1", APPROVED, "5A", "boulder", []), ("R2", APPROVED, "6A", "boulder", [])],
                              status="rejected", uploader=self.owner)
        self.wall.review_note = "Photo too dark"
        self.wall.save()

    def get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)

    def test_nobody_but_admins_can_open_it(self):
        wid = self.wall.wall_id
        for user in (self.owner, self.other):
            self.assertEqual(self.get(user, f"/ar/api/walls/{wid}/unity").status_code, 404, user.username)
            self.assertEqual(self.get(user, f"/ar/walls/{wid}/routes/").status_code, 404, user.username)
            self.assertEqual(self.get(user, f"/ar/wall/{wid}/").status_code, 404, user.username)
        doc = self.get(self.admin, f"/ar/api/walls/{wid}/unity").json()
        self.assertEqual({r["id"] for r in doc["routes"]}, {"R1", "R2"})
        self.assertEqual(self.get(self.admin, f"/ar/wall/{wid}/").status_code, 200)

    def test_uploader_still_sees_it_listed_as_rejected_with_the_note(self):
        from .records import filter_wall_list
        mine = filter_wall_list(self.owner, [{"wall_id": self.wall.wall_id}])
        self.assertEqual(len(mine), 1)
        self.assertEqual((mine[0]["review_status"], mine[0]["openable"], mine[0]["review_note"]), ("rejected", False, "Photo too dark"))
        self.assertEqual(filter_wall_list(self.other, [{"wall_id": self.wall.wall_id}]), [])
        self.assertTrue(filter_wall_list(self.admin, [{"wall_id": self.wall.wall_id}])[0]["openable"])
        # thumbnail for the uploader's list card still loads; other users get nothing
        self.assertEqual(self.get(self.owner, f"/ar/photo/{self.wall.wall_id}").status_code, 200)
        self.assertEqual(self.get(self.other, f"/ar/photo/{self.wall.wall_id}").status_code, 404)

    def test_recognising_a_rejected_wall_by_scanning_reveals_no_routes(self):
        from .records import doc_for
        doc = doc_for(self.other, self.wall, dict(self.wall.doc))
        self.assertEqual(doc["routes"], [])
        self.assertTrue(doc["unavailable"])
        self.assertEqual(doc_for(self.owner, self.wall, dict(self.wall.doc))["routes"], [])
        self.assertEqual(len(doc_for(self.admin, self.wall, dict(self.wall.doc))["routes"]), 2)

    def test_pending_wall_still_opens_for_its_uploader(self):
        pending = make_wall([("R1", APPROVED, "5A", "boulder", [])], status=PENDING, uploader=self.owner)
        self.assertEqual(self.get(self.owner, f"/ar/api/walls/{pending.wall_id}/unity").status_code, 200)
        self.assertEqual(self.get(self.other, f"/ar/api/walls/{pending.wall_id}/unity").status_code, 404)


class DeleteWallTests(IsolatedEngine, TestCase):
    """Admins delete a wall everywhere (engine files + database), so it can never come back."""

    def setUp(self):
        super().setUp()
        import cv2
        import numpy as np
        img = np.full((300, 400, 3), 200, np.uint8)
        cv2.circle(img, (120, 120), 25, (30, 30, 200), -1)
        photo = cv2.imencode(".jpg", img)[1].tobytes()
        self.wid = str(uuid.uuid4())
        doc = {"wall_id": self.wid, "name": "To delete", "venue": "Gym", "mode": "indoor",
               "holds": [{"id": f"H{i}", "x": 0.1 * i, "y": 0.5, "status": "detected"} for i in range(1, 5)],
               "routes": [{"id": "R1", "hold_ids": ["H1", "H2", "H3"], "points": []}]}
        self.sub = WallSubmission.objects.create(wall_id=self.wid, name="To delete", venue="Gym", mode="indoor",
                                                 status=APPROVED, doc=doc, photo=photo)
        RouteReview.objects.create(wall=self.sub, route_id="R1", status=APPROVED, grade="5A")
        self.admin = User.objects.create_user("admin", password="pw-12345678", is_staff=True, is_superuser=True)
        self.user = User.objects.create_user("climber", password="pw-12345678")
        from wallscan.views import _engine
        self.engine = _engine()                                  # restores the wall into the engine
        self.assertIn(self.wid, [w["wall_id"] for w in self.engine.store.search_walls(limit=100)])

    def engine_has_it(self):
        return self.wid in [w["wall_id"] for w in self.engine.store.search_walls(limit=100)]

    def test_admin_delete_removes_record_and_engine_files(self):
        self.client.force_login(self.admin)
        r = self.client.delete(f"/ar/api/walls/{self.wid}?confirm=1")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(WallSubmission.objects.filter(wall_id=self.wid).exists())
        self.assertFalse(RouteReview.objects.filter(route_id="R1", wall_id=self.sub.pk).exists())
        self.assertFalse(self.engine_has_it())
        self.client.get("/ar/review/")                           # the Review page must not bring it back
        self.assertFalse(WallSubmission.objects.filter(wall_id=self.wid).exists())

    def test_delete_still_works_when_engine_files_are_already_gone(self):
        self.engine.store.delete_wall(self.wid, confirm=True)   # e.g. hosting disk wiped
        self.engine.restored_from_db = True                      # and not restored yet
        self.client.force_login(self.admin)
        self.assertEqual(self.client.delete(f"/ar/api/walls/{self.wid}?confirm=1").status_code, 200)
        self.assertFalse(WallSubmission.objects.filter(wall_id=self.wid).exists())

    def test_non_admin_cannot_delete(self):
        self.client.force_login(self.user)
        self.assertIn(self.client.delete(f"/ar/api/walls/{self.wid}?confirm=1").status_code, (401, 403))
        self.assertTrue(WallSubmission.objects.filter(wall_id=self.wid).exists())
        self.assertTrue(self.engine_has_it())

    def test_django_admin_delete_also_removes_engine_files(self):
        from django.contrib import admin as dj_admin
        from wallscan.admin import WallSubmissionAdmin
        WallSubmissionAdmin(WallSubmission, dj_admin.site).delete_model(None, self.sub)
        self.assertFalse(WallSubmission.objects.filter(wall_id=self.wid).exists())
        self.assertFalse(self.engine_has_it())
        self.client.force_login(self.admin)
        self.client.get("/ar/review/")                           # would re-import it if the files were left
        self.assertFalse(WallSubmission.objects.filter(wall_id=self.wid).exists())


class PasswordResetRequestTests(TestCase):
    """#6: the reset page records a request; an administrator sets a new password in the Review queue."""

    NEW_PASSWORD = "Climb-Again-2026"

    def setUp(self):
        self.climber = User.objects.create_user("forgetful", email="Forget@Example.com", password="old-password-123")
        self.admin = User.objects.create_user("boss", password="admin-pass-123", is_staff=True)

    def ask(self, email):
        return self.client.post("/api/auth/password-reset/", {"email": email}, content_type="application/json")

    def act(self, user, action, password=""):
        from .models import PasswordResetRequest
        req = PasswordResetRequest.objects.get(user=self.climber)
        self.client.force_login(user)
        return self.client.post(f"/ar/review/password-resets/{req.pk}/", {"action": action, "password": password},
                                content_type="application/json")

    def test_request_is_recorded_once_and_repeats_are_merged(self):
        from .models import PasswordResetRequest
        first, second = self.ask("forget@example.com"), self.ask("forget@example.com")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        reqs = PasswordResetRequest.objects.filter(user=self.climber, done=False)
        self.assertEqual(reqs.count(), 1)
        self.assertEqual(reqs.get().times_requested, 2)

    def test_unknown_or_inactive_email_records_nothing_and_looks_the_same(self):
        from .models import PasswordResetRequest
        User.objects.create_user("gone", email="gone@example.com", password="x-password-123", is_active=False)
        known, unknown, inactive = self.ask("forget@example.com"), self.ask("nobody@example.com"), self.ask("gone@example.com")
        self.assertEqual(known.json(), unknown.json())
        self.assertEqual(known.json(), inactive.json())
        self.assertEqual(PasswordResetRequest.objects.count(), 1)

    def test_admin_sees_the_request_and_the_badge_counts_it(self):
        self.ask("forget@example.com")
        self.client.force_login(self.admin)
        page = self.client.get("/ar/review/")
        self.assertContains(page, "Password reset requests")
        self.assertContains(page, "forgetful")
        self.assertGreaterEqual(page.context["review_pending"], 1)

    def test_admin_sets_a_new_password_and_the_climber_can_log_in(self):
        from .models import PasswordResetRequest
        self.ask("forget@example.com")
        r = self.act(self.admin, "set_password", self.NEW_PASSWORD)
        self.assertEqual(r.status_code, 200, r.content)
        self.client.logout()
        self.assertTrue(self.client.login(username="forgetful", password=self.NEW_PASSWORD))
        req = PasswordResetRequest.objects.get(user=self.climber)
        self.assertEqual((req.done, req.outcome, req.handled_by), (True, PasswordResetRequest.PASSWORD_SET, self.admin))

    def test_weak_password_is_refused_and_the_request_stays_open(self):
        from .models import PasswordResetRequest
        self.ask("forget@example.com")
        self.assertEqual(self.act(self.admin, "set_password", "12345678").status_code, 400)
        self.assertFalse(PasswordResetRequest.objects.get(user=self.climber).done)

    def test_dismiss_keeps_the_old_password(self):
        from .models import PasswordResetRequest
        self.ask("forget@example.com")
        self.assertEqual(self.act(self.admin, "dismiss").status_code, 200)
        self.assertEqual(PasswordResetRequest.objects.get(user=self.climber).outcome, PasswordResetRequest.DISMISSED)
        self.client.logout()
        self.assertTrue(self.client.login(username="forgetful", password="old-password-123"))

    def test_only_administrators_can_handle_requests(self):
        from .models import PasswordResetRequest
        self.ask("forget@example.com")
        self.assertEqual(self.act(self.climber, "set_password", self.NEW_PASSWORD).status_code, 403)
        self.assertFalse(PasswordResetRequest.objects.get(user=self.climber).done)
