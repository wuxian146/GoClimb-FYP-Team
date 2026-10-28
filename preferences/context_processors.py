def climber_preferences(request):
    """Tells base.html whether to ask a logged-in climber for their region & interests."""
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return {}
    pref = getattr(user, "climber_preference", None)
    has_experience = bool(getattr(getattr(user, "climber_profile", None), "experience_level", ""))
    return {
        "climber_pref": pref,
        # ask after the team's experience-level question has been answered
        "ask_climber_prefs": has_experience and (pref is None or not pref.complete)
                             and not request.path.startswith("/profile/preferences"),
    }
