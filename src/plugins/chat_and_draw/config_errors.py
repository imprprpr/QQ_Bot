"""Log managed-config read failures once per distinct problem.

The hot-read config files fail closed (features off) when unreadable. That default is
safe, but it must be visible: otherwise one bad save silently stops group chat.
"""
import threading

_seen = {}
_lock = threading.Lock()


def report(name, path, exc):
    reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    with _lock:
        if _seen.get(name) == reason:
            return
        _seen[name] = reason
    from nonebot import logger
    logger.error("{} config unreadable, related features disabled ({}): {}", name, path, reason)


def clear(name):
    with _lock:
        _seen.pop(name, None)


def current():
    """Active config problems as {name: reason}, for status pages."""
    with _lock:
        return dict(_seen)
