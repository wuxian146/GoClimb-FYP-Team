from django.contrib import admin

from .models import ArAppToken, RouteReview, WallSubmission


class RouteReviewInline(admin.TabularInline):
    model = RouteReview
    extra = 0
    fields = ("route_id", "status", "edited_by_admin", "note", "reviewed_by", "reviewed_at")
    readonly_fields = ("reviewed_by", "reviewed_at")


@admin.register(WallSubmission)
class WallSubmissionAdmin(admin.ModelAdmin):
    list_display = ("name", "venue", "status", "source", "uploaded_by", "route_count", "created_at")
    list_filter = ("status", "source", "mode")
    search_fields = ("name", "venue", "wall_id", "uploaded_by__username")
    readonly_fields = ("wall_id", "hold_count", "route_count", "created_at", "updated_at")
    inlines = [RouteReviewInline]

    # Also remove the route engine's files, or the Review page would re-import the wall as a new record.
    @staticmethod
    def _delete_engine_files(wall_ids):
        from .views import _engine
        engine = _engine()
        for wid in wall_ids:
            try:
                engine.store.delete_wall(wid, confirm=True)
            except Exception:
                pass                                   # already gone

    def delete_model(self, request, obj):
        self._delete_engine_files([obj.wall_id])
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        self._delete_engine_files(list(queryset.values_list("wall_id", flat=True)))
        super().delete_queryset(request, queryset)


@admin.register(ArAppToken)
class ArAppTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "last_used_at")
    readonly_fields = ("key",)
