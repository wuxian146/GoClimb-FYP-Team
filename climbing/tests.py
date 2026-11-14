from datetime import timedelta
import re
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ClimbSchedule, ClimberProfile, Crag, Route


class ClimberJourneyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('climber', password='safe-password-1')
        self.crag = Crag.objects.create(name='Test Wall', slug='test-wall')
        Route.objects.create(crag=self.crag, name='Practice Line', grade='4A')
        self.client.force_login(self.user)

    def test_future_climb_is_saved_and_confirmed(self):
        future = timezone.localtime(timezone.now() + timedelta(days=2))
        response = self.client.post(reverse('schedule', args=[self.crag.id]), {
            'climb_date': future.strftime('%Y-%m-%d'), 'climb_time': future.strftime('%H:%M'),
            'friends': 'Alex', 'notes': 'Bring chalk',
        })
        self.assertContains(response, 'Your climb is confirmed')
        self.assertEqual(ClimbSchedule.objects.filter(user=self.user, crag=self.crag).count(), 1)
        self.assertContains(self.client.get(reverse('profile')), 'Test Wall')

    @patch('climbing.crag_views.live_singapore_weather')
    def test_weather_endpoint_returns_live_provider_shape(self, weather):
        weather.return_value = {'location': 'Singapore', 'current': {'temperature': 30}, 'forecast': []}
        response = self.client.get(reverse('api_crag_weather', args=[self.crag.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['results']['location'], 'Singapore')
        weather.assert_called_once()

    def test_reset_request_uses_a_generic_local_notice(self):
        response = self.client.post(reverse('api_password_reset'), {'email': self.user.email}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertNotIn('reset_url', response.json())

    def test_legacy_home_page_is_not_routable(self):
        self.assertEqual(self.client.get('/home/').status_code, 404)

    def test_anonymous_schedule_uses_the_project_login_and_returns_here(self):
        self.client.logout()
        schedule_url = reverse('schedule', args=[self.crag.id])
        response = self.client.get(schedule_url)
        self.assertRedirects(response, f'/login/?next={schedule_url}')

    def test_anonymous_detail_hides_ar_and_scheduling(self):
        self.client.logout()
        response = self.client.get(reverse('crag_detail', args=[self.crag.slug]))
        self.assertNotContains(response, 'onclick="openAR(', html=False)
        self.assertNotContains(response, 'Schedule a Climb Here')

    def test_admin_login_accepts_a_fresh_csrf_token(self):
        User.objects.create_superuser('presentation-admin', 'admin@example.com', 'safe-password-2')
        csrf_client = Client(enforce_csrf_checks=True)
        admin_login_url = f"{reverse('admin:login')}?next=/admin/"
        login_page = csrf_client.get(admin_login_url)
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', login_page.content.decode())
        self.assertIsNotNone(match)
        response = csrf_client.post(admin_login_url, {
            'username': 'presentation-admin',
            'password': 'safe-password-2',
            'csrfmiddlewaretoken': match.group(1),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/admin/')

    def test_experience_is_requested_then_saved_once(self):
        profile = ClimberProfile.objects.create(user=self.user)
        response = self.client.post(reverse('api_experience_level'),
                                    {'experience_level': 'experienced'}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertEqual(profile.experience_level, 'experienced')
        self.assertContains(self.client.get(reverse('profile')), 'Experienced')

    def test_staff_dashboard_rejects_regular_users_and_supports_crag_create(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('admin_dashboard')).status_code, 403)
        staff = User.objects.create_user('staff', password='safe-password-3', is_staff=True)
        self.client.force_login(staff)
        dashboard = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(dashboard.status_code, 200)
        create = self.client.post(reverse('admin_create', args=['crags']), {
            'name': 'Admin Test Crag', 'slug': 'admin-test-crag', 'rock_type': 'granite',
            'difficulty_min': '4A', 'difficulty_max': '6B', 'length_m': 20,
            'lat': '1.3521', 'lng': '103.8198',
        })
        self.assertEqual(create.status_code, 302)
        self.assertTrue(Crag.objects.filter(slug='admin-test-crag').exists())

    def test_marketing_landing_page_links_to_registration(self):
        response = self.client.get(reverse('root'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Create a free account')
        self.assertContains(response, 'href="/register/"', html=False)

    def test_removed_legacy_ar_assets_are_not_routable(self):
        response = self.client.get('/ar-assets/HIRO.jpg')
        self.assertEqual(response.status_code, 404)


class DashboardClimbsTests(TestCase):
    """#33: the dashboard lists the climber's upcoming and past scheduled climbs."""

    def test_dashboard_shows_upcoming_and_past_climbs(self):
        from datetime import timedelta
        from django.utils import timezone
        from .models import ClimbSchedule, Crag
        user = User.objects.create_user("dash-climber", password="safe-password-9")
        crag = Crag.objects.create(name="Dashboard Test Crag", slug="dashboard-test-crag", lat=1.35, lng=103.8)
        ClimbSchedule.objects.create(user=user, crag=crag, climb_at=timezone.now() + timedelta(days=2), friends="Sam")
        ClimbSchedule.objects.create(user=user, crag=crag, climb_at=timezone.now() - timedelta(days=5), notes="Sent the slab")
        self.client.force_login(user)
        page = self.client.get("/dashboard/").content.decode()
        self.assertIn("Your Climbs", page)
        self.assertIn("Upcoming", page)
        self.assertIn("with Sam", page)
        self.assertIn("Sent the slab", page)
