import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import ENVIRONMENTS, HOLD_TYPES, REGIONS, STYLES, ClimberPreference

CHOICES = {"regions": REGIONS, "hold_types": HOLD_TYPES, "styles": STYLES, "environments": ENVIRONMENTS}


def _clean(data):
    keys = lambda pairs: {k for k, _ in pairs}
    region = data.get("region") or ""
    if region not in keys(REGIONS):
        raise ValueError("Choose your region.")
    environment = data.get("environment") or ""
    if environment not in keys(ENVIRONMENTS):
        raise ValueError("Choose indoor, outdoor or both.")
    holds = [h for h in (data.get("hold_types") or []) if h in keys(HOLD_TYPES)]
    styles = [s for s in (data.get("styles") or []) if s in keys(STYLES)]
    if not holds and not styles:
        raise ValueError("Pick at least one hold type or climbing style.")
    return {"region": region, "region_other": (data.get("region_other") or "").strip()[:80] if region == "other" else "",
            "environment": environment, "hold_types": holds, "styles": styles}


@login_required(login_url="/login/")
def preferences_page(request):
    pref, _ = ClimberPreference.objects.get_or_create(user=request.user)
    error = saved = None
    if request.method == "POST":
        data = {"region": request.POST.get("region"), "region_other": request.POST.get("region_other"),
                "environment": request.POST.get("environment"),
                "hold_types": request.POST.getlist("hold_types"), "styles": request.POST.getlist("styles")}
        try:
            for k, v in _clean(data).items():
                setattr(pref, k, v)
            pref.save()
            nxt = request.GET.get("next", "")
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            saved = True
        except ValueError as exc:
            error = str(exc)
    return render(request, "preferences/page.html", dict(CHOICES, pref=pref, error=error, saved=saved))


@login_required(login_url="/login/")
@require_POST
def preferences_api(request):
    try:
        cleaned = _clean(json.loads(request.body or b"{}"))
    except (ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    pref, _ = ClimberPreference.objects.update_or_create(user=request.user, defaults=cleaned)
    return JsonResponse({"success": True, "region": pref.region_label()})
