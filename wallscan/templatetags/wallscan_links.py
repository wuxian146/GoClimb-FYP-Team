"""{% load wallscan_links %}{% route_wall route as link %}: the real scanned wall for a crag route, if
this user may see it (approved wall + approved route, or their own / admin)."""
from django import template

from wallscan import records
from wallscan.models import APPROVED, RouteReview, RouteWallLink

register = template.Library()


def visible_link(user, route):
    link = RouteWallLink.objects.select_related("wall").filter(route=route).first()
    if link is None or not records.wall_visible(user, link.wall):
        return None
    if records.can_manage(user, link.wall):
        return link
    review = RouteReview.objects.filter(wall=link.wall, route_id=link.wall_route_id).first()
    return link if review and review.status == APPROVED else None


@register.simple_tag(takes_context=True)
def route_wall(context, route):
    request = context.get("request")
    return visible_link(getattr(request, "user", None), route)
