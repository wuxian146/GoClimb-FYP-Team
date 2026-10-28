"""Calendar export for booked climbs.

- .ics file: works with Apple Calendar, Outlook, Samsung/OPPO calendars, Google Calendar import
- Google Calendar link: one tap, no login integration needed on our side
"""
from datetime import timezone as dt_timezone
from urllib.parse import urlencode

from django.utils import timezone


def _utc(dt):
    return dt.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _esc(text):
    """RFC 5545 text escaping."""
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line):
    """RFC 5545: lines longer than 75 octets are folded with CRLF + space."""
    out, raw = [], line.encode("utf-8")
    while len(raw) > 75:
        cut = 75
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:      # don't split a UTF-8 character
            cut -= 1
        out.append(raw[:cut].decode("utf-8"))
        raw = b" " + raw[cut:]
    out.append(raw.decode("utf-8"))
    return "\r\n".join(out)


def describe(meetup, people):
    lines = [f"Climbing session planned on GoClimb by {meetup.organizer.get_username()}."]
    if people:
        lines.append("Climbers: " + ", ".join(people))
    if meetup.note:
        lines.append("Note: " + meetup.note)
    return "\n".join(lines)


def location(meetup):
    loc = meetup.place_label()
    if meetup.crag_id:
        return f"{loc} ({float(meetup.crag.lat):.6f}, {float(meetup.crag.lng):.6f})"
    if meetup.lat is not None and meetup.lng is not None:
        return f"{loc} ({meetup.lat:.6f}, {meetup.lng:.6f})"
    return loc


def ics(meetup, people, host):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GoClimb//Climbing plans//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:goclimb-meetup-{meetup.pk}@{host}",
        f"DTSTAMP:{_utc(timezone.now())}",
        f"DTSTART:{_utc(meetup.starts_at)}",
        f"DTEND:{_utc(meetup.ends_at)}",
        f"SUMMARY:{_esc(meetup.title)}",
        f"LOCATION:{_esc(location(meetup))}",
        f"DESCRIPTION:{_esc(describe(meetup, people))}",
        "STATUS:" + ("CANCELLED" if meetup.cancelled else "CONFIRMED"),
        "BEGIN:VALARM",
        "TRIGGER:-PT1H",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_esc(meetup.title)} in 1 hour",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def google_link(meetup, people):
    return "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE",
        "text": meetup.title,
        "dates": f"{_utc(meetup.starts_at)}/{_utc(meetup.ends_at)}",
        "details": describe(meetup, people),
        "location": location(meetup),
    })
