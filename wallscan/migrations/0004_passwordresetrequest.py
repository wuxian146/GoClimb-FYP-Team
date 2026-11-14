import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wallscan", "0003_routereview_grade_routereview_hold_types_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PasswordResetRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("times_requested", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_requested_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("done", models.BooleanField(default=False)),
                ("outcome", models.CharField(blank=True, max_length=20)),
                ("handled_at", models.DateTimeField(blank=True, null=True)),
                ("handled_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name="+", to=settings.AUTH_USER_MODEL)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name="password_reset_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-last_requested_at"]},
        ),
    ]
