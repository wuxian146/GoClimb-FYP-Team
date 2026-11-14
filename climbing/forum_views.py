"""Community forum page views + JSON API (DRF, session-based auth)."""

from django.shortcuts import render, redirect
from django.views.decorators.csrf import ensure_csrf_cookie

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

from django.contrib.auth.models import User

from .models import ForumPost, ForumComment, ForumLike, Favorite
from .serializers import ForumPostSerializer, ForumCommentSerializer


# ---------------------------------------------------------------------------
# Page views (HTML templates)
# ---------------------------------------------------------------------------

@ensure_csrf_cookie
def forum_page(request):
    crag_slug = request.GET.get('crag')
    return render(request, 'forum/forum.html', {'crag_slug': crag_slug or ''})


@ensure_csrf_cookie
def dashboard_page(request):
    if not request.user.is_authenticated:
        return redirect('login')
    user = request.user

    favorites = (
        Favorite.objects.filter(user=user)
        .select_related('crag')
        .order_by('-created_at')
    )
    my_posts = ForumPost.objects.filter(author=user).order_by('-created_at')
    my_comments = (
        ForumComment.objects.filter(author=user)
        .select_related('post')
        .order_by('-created_at')[:6]
    )

    # Rock-type distribution across saved crags (for a quick bar chart).
    rock_counts = {}
    for f in favorites:
        rt = f.crag.get_rock_type_display()
        rock_counts[rt] = rock_counts.get(rt, 0) + 1

    from django.utils import timezone
    now = timezone.now()
    ctx = {
        'username': user.username,
        'upcoming_climbs': user.climb_schedules.select_related('crag').filter(climb_at__gte=now).order_by('climb_at')[:6],
        'past_climbs': user.climb_schedules.select_related('crag').filter(climb_at__lt=now).order_by('-climb_at')[:6],
        'favorites': favorites,
        'fav_count': favorites.count(),
        'my_posts': my_posts[:6],
        'post_count': my_posts.count(),
        'my_comments': my_comments,
        'comment_count': ForumComment.objects.filter(author=user).count(),
        'like_count': ForumLike.objects.filter(user=user).count(),
        'rock_counts': rock_counts,
        'rock_total': sum(rock_counts.values()),
    }
    return render(request, 'crags/dashboard.html', ctx)


# ---------------------------------------------------------------------------
# API: Forum posts (list + create)
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def forum_post_list_api(request):
    if request.method == 'GET':
        qs = ForumPost.objects.all()
        crag_slug = request.GET.get('crag')
        author = request.GET.get('author')
        if crag_slug:
            qs = qs.filter(crag__slug=crag_slug)
        if author:
            qs = qs.filter(author__username=author)
        serializer = ForumPostSerializer(
            qs, many=True, context={'request': request}
        )
        return Response({'success': True, 'count': qs.count(), 'results': serializer.data})

    # POST -> create
    if not request.user.is_authenticated:
        return Response({'error': 'Please log in before posting'}, status=status.HTTP_401_UNAUTHORIZED)
    title = (request.data.get('title') or '').strip()
    content = (request.data.get('content') or '').strip()
    if not title or not content:
        return Response({'error': 'Both the title and content are required'}, status=status.HTTP_400_BAD_REQUEST)
    crag_id = request.data.get('crag')
    post = ForumPost.objects.create(
        author=request.user, title=title, content=content, crag_id=crag_id or None
    )
    serializer = ForumPostSerializer(post, context={'request': request})
    return Response(serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# API: Comments (list + create) for a post
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def forum_comment_api(request, pk):
    post = ForumPost.objects.filter(pk=pk).first()
    if not post:
        return Response({'error': 'The post does not exist'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'GET':
        comments = post.comments.all()
        data = ForumCommentSerializer(comments, many=True).data
        return Response({'success': True, 'count': comments.count(), 'results': data})

    # POST -> add comment
    if not request.user.is_authenticated:
        return Response({'error': 'Please log in before commenting'}, status=status.HTTP_401_UNAUTHORIZED)
    content = (request.data.get('content') or '').strip()
    if not content:
        return Response({'error': 'Comment content can not be empty'}, status=status.HTTP_400_BAD_REQUEST)
    comment = ForumComment.objects.create(
        post=post, author=request.user, content=content
    )
    return Response(
        ForumCommentSerializer(comment).data, status=status.HTTP_201_CREATED
    )


# ---------------------------------------------------------------------------
# API: Like toggle for a post
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def forum_like_api(request, pk):
    post = ForumPost.objects.filter(pk=pk).first()
    if not post:
        return Response({'error': 'The post does not exist'}, status=status.HTTP_404_NOT_FOUND)
    like, created = ForumLike.objects.get_or_create(user=request.user, post=post)
    if not created:
        like.delete()
    return Response({
        'success': True,
        'is_liked': created,
        'like_count': post.likes.count(),
    })
