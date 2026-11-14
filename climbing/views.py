from datetime import datetime

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.forms import ModelForm, CharField, PasswordInput, DateTimeInput
from django.shortcuts import get_object_or_404

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

from .serializers import UserSerializer
from .models import ClimberProfile, ClimbSchedule, Crag, EXPERIENCE_LEVEL_CHOICES
from .models import Route, Favorite, ForumPost, ForumComment, ForumLike


def staff_required(view):
    """Redirect anonymous visitors to sign in, but explicitly deny members."""
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            return HttpResponseForbidden('Administrator access is required.')
        return view(request, *args, **kwargs)
    return wrapped


# ============================================================
# Page rendering views (HTML templates — unchanged GET behavior)
# ============================================================

@ensure_csrf_cookie
def login_view(request):
    """Render the login page. Form submission is handled via fetch() to /api/auth/login/."""
    return render(request, 'auth/login.html')


@ensure_csrf_cookie
def register_view(request):
    """Render the registration page. Form submission is handled via fetch() to /api/auth/register/."""
    return render(request, 'auth/register.html')


@ensure_csrf_cookie
def password_reset_view(request):
    """Render the password-reset request page. Handled via fetch() to /api/auth/password-reset/."""
    return render(request, 'auth/password_reset.html')


def crag_detail_view(request, crag_id):
    from .models import Crag
    from django.shortcuts import redirect
    crag = Crag.objects.filter(pk=crag_id).first()
    if not crag:
        return redirect('search')
    return redirect('crag_detail', slug=crag.slug)


@login_required
def profile_view(request):
    profile, _ = ClimberProfile.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        profile.display_name = request.POST.get('display_name', '').strip()
        profile.bio = request.POST.get('bio', '').strip()
        profile.home_gym = request.POST.get('home_gym', '').strip()
        birthday = request.POST.get('birthday')
        since = request.POST.get('climbing_since')
        profile.birthday = birthday or None
        profile.climbing_since = int(since) if since and since.isdigit() else None
        experience_level = request.POST.get('experience_level', '')
        profile.experience_level = experience_level if experience_level in dict(EXPERIENCE_LEVEL_CHOICES) else ''
        profile.save()
        return redirect('profile')
    return render(request, 'auth/profile.html', {
        'profile': profile,
        'upcoming_climbs': request.user.climb_schedules.select_related('crag').filter(climb_at__gte=timezone.now()),
        'past_climbs': request.user.climb_schedules.select_related('crag').filter(climb_at__lt=timezone.now()).order_by('-climb_at')[:8],
    })


@login_required
def schedule_view(request, crag_id):
    crag = Crag.objects.filter(pk=crag_id).first()
    if not crag:
        return redirect('search')
    if request.method == 'POST':
        try:
            date_value = request.POST['climb_date']
            time_value = request.POST['climb_time']
            climb_at = timezone.make_aware(datetime.strptime(date_value + ' ' + time_value, '%Y-%m-%d %H:%M'))
        except (KeyError, ValueError):
            return render(request, 'crags/schedule.html', {'crag': crag, 'error': 'Please choose a valid date and time.'})
        if climb_at < timezone.now():
            return render(request, 'crags/schedule.html', {'crag': crag, 'error': 'Please choose a future time.'})
        climb = ClimbSchedule.objects.create(
            user=request.user, crag=crag, climb_at=climb_at,
            friends=request.POST.get('friends', '').strip(), notes=request.POST.get('notes', '').strip(),
        )
        return render(request, 'crags/schedule.html', {'crag': crag, 'confirmed_climb': climb})
    return render(request, 'crags/schedule.html', {'crag': crag})


# ============================================================
# Authentication API (DRF + JSON, Session-based)
# ============================================================

@api_view(['POST'])
@permission_classes([AllowAny])
def api_login(request):
    identifier = request.data.get('username') or request.data.get('email')
    password = request.data.get('password')
    if not identifier or not password:
        return Response({'error': 'Please provide your account and password'}, status=status.HTTP_400_BAD_REQUEST)
    user = authenticate(request, username=identifier, password=password)
    if user is None:
        # Allow login by email as well as username.
        try:
            candidate = User.objects.get(email=identifier)
            user = authenticate(request, username=candidate.username, password=password)
        except User.DoesNotExist:
            user = None
    if user is None:
        return Response({'error': 'Incorrect username or password'}, status=status.HTTP_401_UNAUTHORIZED)
    login(request, user)
    profile, _ = ClimberProfile.objects.get_or_create(user=user)
    return Response({'success': True, 'user': UserSerializer(user).data,
                     'needs_experience': not bool(profile.experience_level)})


@api_view(['POST'])
@permission_classes([AllowAny])
def api_register(request):
    username = request.data.get('username')
    email = request.data.get('email')
    password = request.data.get('password')
    password_confirm = request.data.get('password_confirm')

    if not all([username, email, password, password_confirm]):
        return Response({'error': 'All fields are required'}, status=status.HTTP_400_BAD_REQUEST)
    if password != password_confirm:
        return Response({'error': 'passwords you entered dont match'}, status=status.HTTP_400_BAD_REQUEST)
    if User.objects.filter(username=username).exists():
        return Response({'error': 'This username is already taken'}, status=status.HTTP_400_BAD_REQUEST)
    if User.objects.filter(email=email).exists():
        return Response({'error': 'This email has already been registered'}, status=status.HTTP_400_BAD_REQUEST)

    user = User.objects.create_user(username=username, email=email, password=password)
    ClimberProfile.objects.create(user=user)
    login(request, user)
    return Response({'success': True, 'user': UserSerializer(user).data,
                     'needs_experience': True}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_experience_level(request):
    """Persist the required first-login climbing experience choice."""
    value = (request.data.get('experience_level') or '').strip().lower()
    if value not in dict(EXPERIENCE_LEVEL_CHOICES):
        return Response({'error': 'Choose Beginner, Experienced, or Master.'}, status=status.HTTP_400_BAD_REQUEST)
    profile, _ = ClimberProfile.objects.get_or_create(user=request.user)
    profile.experience_level = value
    profile.save(update_fields=['experience_level'])
    return Response({'success': True, 'experience_level': profile.get_experience_level_display()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_logout(request):
    logout(request)
    return Response({'success': True, 'message': 'Successfully logged out'})


@api_view(['POST'])
@permission_classes([AllowAny])
def api_password_reset(request):
    """Generate a signed reset link. No SMTP wired up — link is returned/printed locally."""
    email = request.data.get('email')
    # GoClimb has no email service: record a request that administrators handle in the Review queue.
    # The reply is the same whatever was typed, so the page never reveals which emails have an account.
    from wallscan.password_resets import record_request
    record_request(email)
    return Response({'success': True, 'message': 'Your request has been received. An administrator will restore access to your account.'})


@api_view(['POST'])
@permission_classes([AllowAny])
def api_password_reset_confirm(request):
    """Set a new password given a valid uid + token from the reset link."""
    uid_b64 = request.data.get('uid')
    token = request.data.get('token')
    new_password = request.data.get('new_password')
    if not all([uid_b64, token, new_password]):
        return Response({'error': 'uid、token Both the old and new passwords are required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid_b64)))
    except (User.DoesNotExist, ValueError, TypeError):
        return Response({'error': 'Invalid reset link'}, status=status.HTTP_400_BAD_REQUEST)
    if not default_token_generator.check_token(user, token):
        return Response({'error': 'The reset link has expired, please request it again.'}, status=status.HTTP_400_BAD_REQUEST)
    user.set_password(new_password)
    user.save()
    return Response({'success': True, 'message': 'Your password has been reset, please log in again'})


# ============================================================
# Custom staff administration dashboard
# ============================================================

class UserManagementForm(ModelForm):
    password = CharField(required=False, widget=PasswordInput(render_value=False),
                         help_text='Leave blank to keep the current password.')

    class Meta:
        model = User
        fields = ('username', 'email', 'first_name', 'last_name', 'is_active', 'is_staff')

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)
        elif not user.pk:
            user.set_unusable_password()
        if commit:
            user.save()
        return user


class ProfileManagementForm(ModelForm):
    class Meta:
        model = ClimberProfile
        fields = ('user', 'display_name', 'birthday', 'bio', 'home_gym', 'climbing_since', 'experience_level')


class CragManagementForm(ModelForm):
    class Meta:
        model = Crag
        fields = ('name', 'slug', 'description', 'lat', 'lng', 'rock_type', 'difficulty_min',
                  'difficulty_max', 'length_m', 'hazards', 'image')

    def clean(self):
        # The easiest grade can't be harder than the hardest grade (otherwise search can't find the crag)
        data = super().clean()
        from preferences.grades import rank
        lo, hi = rank(data.get('difficulty_min')), rank(data.get('difficulty_max'))
        if lo is not None and hi is not None and lo > hi:
            self.add_error('difficulty_max', "The hardest grade can't be easier than the easiest grade.")
        return data


class RouteManagementForm(ModelForm):
    class Meta:
        model = Route
        fields = ('crag', 'name', 'grade', 'route_type', 'length_m', 'description')


class FavoriteManagementForm(ModelForm):
    class Meta:
        model = Favorite
        fields = ('user', 'crag')


class ScheduleManagementForm(ModelForm):
    class Meta:
        model = ClimbSchedule
        fields = ('user', 'crag', 'climb_at', 'friends', 'notes')
        widgets = {'climb_at': DateTimeInput(attrs={'type': 'datetime-local'})}


class PostManagementForm(ModelForm):
    class Meta:
        model = ForumPost
        fields = ('author', 'title', 'content', 'crag')


class CommentManagementForm(ModelForm):
    class Meta:
        model = ForumComment
        fields = ('post', 'author', 'content')


class LikeManagementForm(ModelForm):
    class Meta:
        model = ForumLike
        fields = ('user', 'post')


ADMIN_ENTITIES = {
    'users': ('Users', User, UserManagementForm, ('username', 'email', 'is_active', 'is_staff')),
    'profiles': ('Climber Profiles', ClimberProfile, ProfileManagementForm, ('user', 'display_name', 'experience_level', 'home_gym')),
    'crags': ('Crags', Crag, CragManagementForm, ('name', 'rock_type', 'difficulty_min', 'difficulty_max')),
    'routes': ('Routes', Route, RouteManagementForm, ('name', 'crag', 'grade', 'route_type')),
    'favorites': ('Favorites', Favorite, FavoriteManagementForm, ('user', 'crag', 'created_at')),
    'schedules': ('Climbing Schedules', ClimbSchedule, ScheduleManagementForm, ('user', 'crag', 'climb_at', 'friends')),
    'posts': ('Community Posts', ForumPost, PostManagementForm, ('title', 'author', 'crag', 'created_at')),
    'comments': ('Community Comments', ForumComment, CommentManagementForm, ('post', 'author', 'created_at')),
    'likes': ('Community Likes', ForumLike, LikeManagementForm, ('user', 'post', 'created_at')),
}


def _admin_entity(entity):
    if entity not in ADMIN_ENTITIES:
        from django.http import Http404
        raise Http404('Unknown management section')
    return ADMIN_ENTITIES[entity]


@staff_required
def admin_dashboard(request):
    entity = request.GET.get('section', 'users')
    title, model, form_class, columns = _admin_entity(entity)
    query = request.GET.get('q', '').strip()
    objects = model.objects.all()
    if query:
        if entity == 'users':
            from django.db.models import Q
            objects = objects.filter(Q(username__icontains=query) | Q(email__icontains=query))
        elif entity in ('crags', 'routes', 'posts'):
            objects = objects.filter(name__icontains=query) if entity != 'posts' else objects.filter(title__icontains=query)
    object_list = list(objects[:100])
    rows = [(obj, [getattr(obj, column, '') for column in columns]) for obj in object_list]
    return render(request, 'admin_dashboard.html', {
        'entities': ADMIN_ENTITIES, 'entity': entity, 'section_title': title,
        'columns': columns, 'rows': rows, 'query': query,
        'counts': {key: spec[1].objects.count() for key, spec in ADMIN_ENTITIES.items()},
    })


@staff_required
def admin_edit(request, entity, pk=None):
    title, model, form_class, columns = _admin_entity(entity)
    instance = get_object_or_404(model, pk=pk) if pk else None
    form = form_class(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect(f'/manage/?section={entity}')
    return render(request, 'admin_edit.html', {
        'form': form, 'entity': entity, 'section_title': title,
        'object': instance, 'is_new': instance is None,
    })


@staff_required
def admin_delete(request, entity, pk):
    title, model, form_class, columns = _admin_entity(entity)
    instance = get_object_or_404(model, pk=pk)
    if request.method == 'POST':
        instance.delete()
        return redirect(f'/manage/?section={entity}')
    return render(request, 'admin_delete.html', {'entity': entity, 'section_title': title, 'object': instance})
