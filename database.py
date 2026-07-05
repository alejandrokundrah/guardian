import os
import json
import secrets
import uuid
from datetime import date, datetime

from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, Text, String,
    DateTime
)
from sqlalchemy import select

# DB configuration
DB_PATH = os.environ.get('DB_PATH', 'guardian.db')
DATABASE_URL = os.environ.get('DATABASE_URL')

# Create SQLAlchemy engine: prefer DATABASE_URL (Postgres), else SQLite file
if DATABASE_URL:
    ENGINE = create_engine(DATABASE_URL)
else:
    ENGINE = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

metadata = MetaData()

# Table definitions (compatible with both SQLite and Postgres)
students = Table('students', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('name', Text, nullable=False),
    Column('token', Text, nullable=False, unique=True),
    Column('allowed_days', Text, nullable=True),
    Column('start_hour', Integer, nullable=True),
    Column('end_hour', Integer, nullable=True),
    Column('daily_limit_minutes', Integer, nullable=True),
    Column('active', Integer, nullable=True),
    Column('emergency_blocked', Integer, nullable=True),
    Column('created_at', DateTime, nullable=True),
)

access_logs = Table('access_logs', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('student_id', Integer, nullable=False),
    Column('domain', Text, nullable=False),
    Column('action', Text, nullable=False),
    Column('timestamp', DateTime, nullable=True),
)

watch_sessions = Table('watch_sessions', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('student_id', Integer, nullable=False),
    Column('date', Text, nullable=False),
    Column('session_start', Text, nullable=False),
    Column('last_query', Text, nullable=False),
)


def get_conn():
    """Return the SQLAlchemy engine for direct use."""
    return ENGINE


def init_db():
    """Create tables if they do not exist."""
    metadata.create_all(ENGINE)


def generate_token():
    return secrets.token_urlsafe(20)


# ── Students ──────────────────────────────────────────────────────────────────

def add_student(name, allowed_days=None, start_hour=19, end_hour=22, daily_limit=120):
    if allowed_days is None:
        allowed_days = [0, 1, 2, 3, 4, 5, 6]
    token = generate_token()
    with ENGINE.begin() as conn:
        res = conn.execute(
            students.insert().values(
                name=name,
                token=token,
                allowed_days=json.dumps(allowed_days),
                start_hour=start_hour,
                end_hour=end_hour,
                daily_limit_minutes=daily_limit,
                active=1,
                emergency_blocked=0,
            )
        )
        sid = res.inserted_primary_key[0]
    return sid, token


def _hydrate(row):
    if row is None:
        return None
    s = dict(row)
    try:
        s['allowed_days'] = json.loads(s.get('allowed_days') or '[]')
    except Exception:
        s['allowed_days'] = []
    return s


def get_all_students():
    with ENGINE.connect() as conn:
        rows = conn.execute(
            select(students).where(students.c.active == 1).order_by(students.c.name)
        ).mappings().all()
    students_list = [_hydrate(r) for r in rows]
    for s in students_list:
        s['minutes_today'] = get_watch_minutes_today(s['id'])
    return students_list


def get_student_by_id(sid):
    with ENGINE.connect() as conn:
        row = conn.execute(
            select(students).where(students.c.id == sid)
        ).mappings().first()
    s = _hydrate(row)
    if s:
        s['minutes_today'] = get_watch_minutes_today(s['id'])
    return s


def get_student_by_token(token):
    with ENGINE.connect() as conn:
        row = conn.execute(
            select(students).where(students.c.token == token).where(students.c.active == 1)
        ).mappings().first()
    return _hydrate(row)


def update_student(sid, **kwargs):
    if 'allowed_days' in kwargs and isinstance(kwargs['allowed_days'], list):
        kwargs['allowed_days'] = json.dumps(kwargs['allowed_days'])
    update_vals = {k: v for k, v in kwargs.items() if v is not None}
    if not update_vals:
        return
    with ENGINE.begin() as conn:
        conn.execute(
            students.update().where(students.c.id == sid).values(**update_vals)
        )


def toggle_emergency_block(sid, blocked):
    with ENGINE.begin() as conn:
        conn.execute(
            students.update().where(students.c.id == sid).values(emergency_blocked=(1 if blocked else 0))
        )


def block_all_students(blocked):
    with ENGINE.begin() as conn:
        conn.execute(students.update().values(emergency_blocked=(1 if blocked else 0)))


def delete_student(sid):
    with ENGINE.begin() as conn:
        conn.execute(students.update().where(students.c.id == sid).values(active=0))


# ── Watch-time tracking ───────────────────────────────────────────────────────

SESSION_GAP_MINUTES = 5   # New session if no query for 5+ min


def record_watch_query(student_id):
    today = date.today().isoformat()
    now   = datetime.now().isoformat()
    with ENGINE.begin() as conn:
        row = conn.execute(
            select(watch_sessions.c.id, watch_sessions.c.last_query)
            .where((watch_sessions.c.student_id == student_id) & (watch_sessions.c.date == today))
            .order_by(watch_sessions.c.id.desc())
            .limit(1)
        ).mappings().first()

        if row:
            diff = (datetime.now() - datetime.fromisoformat(row['last_query'])).total_seconds() / 60
            if diff <= SESSION_GAP_MINUTES:
                conn.execute(
                    watch_sessions.update().where(watch_sessions.c.id == row['id']).values(last_query=now)
                )
            else:
                conn.execute(
                    watch_sessions.insert().values(student_id=student_id, date=today, session_start=now, last_query=now)
                )
        else:
            conn.execute(
                watch_sessions.insert().values(student_id=student_id, date=today, session_start=now, last_query=now)
            )


def get_watch_minutes_today(student_id):
    today = date.today().isoformat()
    with ENGINE.connect() as conn:
        rows = conn.execute(
            select(watch_sessions.c.session_start, watch_sessions.c.last_query)
            .where((watch_sessions.c.student_id == student_id) & (watch_sessions.c.date == today))
        ).mappings().all()

    total = 0.0
    for r in rows:
        start = datetime.fromisoformat(r['session_start'])
        last = datetime.fromisoformat(r['last_query'])
        total += (last - start).total_seconds() / 60

    return round(total, 1)


# ── Access logs ───────────────────────────────────────────────────────────────

def log_access(student_id, domain, action):
    with ENGINE.begin() as conn:
        conn.execute(
            access_logs.insert().values(student_id=student_id, domain=domain, action=action)
        )


def get_logs(student_id=None, limit=100):
    with ENGINE.connect() as conn:
        if student_id:
            q = (select(access_logs, students.c.name.label('student_name'))
                 .select_from(access_logs.join(students, access_logs.c.student_id == students.c.id))
                 .where(access_logs.c.student_id == student_id)
                 .order_by(access_logs.c.timestamp.desc())
                 .limit(limit))
        else:
            q = (select(access_logs, students.c.name.label('student_name'))
                 .select_from(access_logs.join(students, access_logs.c.student_id == students.c.id))
                 .order_by(access_logs.c.timestamp.desc())
                 .limit(limit))
        rows = conn.execute(q).mappings().all()
    return [dict(r) for r in rows]
