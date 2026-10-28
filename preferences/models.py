from django.conf import settings
from django.db import models

REGIONS = [
    ("sg-central", "Singapore - Central"), ("sg-north", "Singapore - North"), ("sg-northeast", "Singapore - North-East"),
    ("sg-east", "Singapore - East"), ("sg-west", "Singapore - West"), ("malaysia", "Malaysia"),
    ("thailand", "Thailand"), ("indonesia", "Indonesia"), ("other", "Other"),
]
HOLD_TYPES = [
    ("jugs", "Jugs"), ("crimps", "Crimps"), ("slopers", "Slopers"), ("pinches", "Pinches"),
    ("pockets", "Pockets"), ("edges", "Edges"), ("volumes", "Volumes"), ("cracks", "Cracks"),
]
STYLES = [
    ("bouldering", "Bouldering"), ("sport", "Sport (lead)"), ("top-rope", "Top rope"), ("trad", "Trad"),
]
ENVIRONMENTS = [("indoor", "Indoor gyms"), ("outdoor", "Outdoor crags"), ("both", "Both")]


class ClimberPreference(models.Model):
    """Where a climber climbs and what they like - used to personalise GoClimb."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, related_name="climber_preference", on_delete=models.CASCADE)
    region = models.CharField(max_length=20, choices=REGIONS, blank=True)
    region_other = models.CharField(max_length=80, blank=True, help_text="When region is Other")
    environment = models.CharField(max_length=10, choices=ENVIRONMENTS, blank=True)
    hold_types = models.JSONField(default=list, blank=True)
    styles = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def complete(self):
        return bool(self.region and self.environment and (self.hold_types or self.styles))

    def region_label(self):
        return self.region_other if self.region == "other" and self.region_other else self.get_region_display()

    def __str__(self):
        return f"{self.user} - {self.region_label() or 'no region'}"
