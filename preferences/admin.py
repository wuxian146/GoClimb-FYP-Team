from django.contrib import admin

from .models import ClimberPreference


@admin.register(ClimberPreference)
class ClimberPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "region", "environment", "updated_at")
    list_filter = ("region", "environment")
    search_fields = ("user__username",)
