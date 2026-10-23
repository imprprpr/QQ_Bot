"""Local-time windows and bounded, persistent proactive eligibility.

Use an explicit UTC offset, independent of the server timezone (default China).
Offsets in daylight-saving regions must be adjusted by the operator.
"""
import os
import random
from datetime import datetime, timedelta, timezone


def local_now():
    return datetime.now(timezone(timedelta(hours=float(os.getenv('PROACTIVE_UTC_OFFSET', '8')))))


def windows():
    if os.getenv('PROACTIVE_MODE', 'windows') == 'random':
        return [(8, 22)]
    return [(8, 10), (12, 14), (19, 22)]


def in_window(now):
    return any(start <= now.hour + now.minute / 60 < end for start, end in windows())


def idle_seconds():
    return max(3600, float(os.getenv('PROACTIVE_IDLE_HOURS', '3')) * 3600)


def next_time(now, last_interaction):
    earliest = max(now.timestamp() + 300, last_interaction + idle_seconds())
    for day in range(3):
        midnight = (now + timedelta(days=day)).replace(hour=0, minute=0, second=0, microsecond=0)
        for start, end in windows():
            lower = max(earliest, (midnight + timedelta(hours=start)).timestamp())
            upper = (midnight + timedelta(hours=end)).timestamp()
            if lower < upper:
                return random.uniform(lower, upper)
    return earliest + 86400


def eligible(session, now):
    count = session.proactive_attempts if session.proactive_day == now.date().isoformat() else 0
    return (in_window(now) and not session.proactive_unanswered
            and now.timestamp() - session.last_interaction >= idle_seconds()
            and count < max(0, min(3, int(os.getenv('PROACTIVE_DAILY_LIMIT', '2')))))
