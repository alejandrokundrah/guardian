from datetime import datetime
from database import (
    get_student_by_token,
    get_watch_minutes_today,
    log_access,
    record_watch_query,
)

# All Netflix-related domains to intercept
NETFLIX_DOMAINS = {
    'netflix.com', 'www.netflix.com', 'api.netflix.com',
    'api-global.netflix.com', 'uiboot.netflix.com',
    'nflxvideo.net', 'nflximg.net', 'nflximg.com',
    'nflxext.com', 'nflxso.net', 'nflxso.com',
    'netflixdnstest.com', 'netflix.net',
    'assets.nflxext.com', 'codex.nflxext.com',
    'fast.com',  # Netflix's speed-test site
}


def is_netflix_domain(domain: str) -> bool:
    d = domain.lower().rstrip('.')
    if d in NETFLIX_DOMAINS:
        return True
    for nd in NETFLIX_DOMAINS:
        if d.endswith('.' + nd):
            return True
    return False


def check_access(domain: str, token: str) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    Only called for Netflix domains.
    """
    student = get_student_by_token(token)
    if not student:
        return False, 'invalid_token'

    sid = student['id']

    # 1. Emergency block
    if student.get('emergency_blocked'):
        log_access(sid, domain, 'blocked_emergency')
        return False, 'emergency_block'

    now = datetime.now()

    # 2. Day check
    if now.weekday() not in student['allowed_days']:
        log_access(sid, domain, 'blocked_day')
        return False, 'not_allowed_today'

    # 3. Time window check
    if not (student['start_hour'] <= now.hour < student['end_hour']):
        log_access(sid, domain, 'blocked_time')
        return False, 'outside_window'

    # 4. Daily watch-limit check
    minutes = get_watch_minutes_today(sid)
    if minutes >= student['daily_limit_minutes']:
        log_access(sid, domain, 'blocked_limit')
        return False, 'limit_reached'

    # ✅ All checks passed
    record_watch_query(sid)
    log_access(sid, domain, 'allowed')
    return True, 'allowed'
