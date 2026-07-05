import sqlite3
import json
import secrets
import uuid
from datetime import date, datetime

DB_PATH = 'guardian.db'


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS students (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT    NOT NULL,
            token                TEXT    UNIQUE NOT NULL,
            allowed_days         TEXT    DEFAULT '[0,1,2,3,4,5,6]',
            start_hour           INTEGER DEFAULT 19,
            end_hour             INTEGER DEFAULT 22,
            daily_limit_minutes  INTEGER DEFAULT 120,
            active               INTEGER DEFAULT 1,
            emergency_blocked    INTEGER DEFAULT 0,
            created_at           TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS access_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id  INTEGER NOT NULL,
            domain      TEXT    NOT NULL,
            action      TEXT    NOT NULL,
            timestamp   TEXT    DEFAULT (datetime('now')),
            FOREIGN KEY (student_id) REFERENCES students(id)
        );

        CREATE TABLE IF NOT EXISTS watch_sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id    INTEGER NOT NULL,
            date          TEXT    NOT NULL,
            session_start TEXT    NOT NULL,
            last_query    TEXT    NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id)
        );
    ''')
    conn.commit()
    conn.close()


def generate_token():
    return secrets.token_urlsafe(20)


# ── Students ──────────────────────────────────────────────────────────────────

def add_student(name, allowed_days=None, start_hour=19, end_hour=22, daily_limit=120):
    if allowed_days is None:
        allowed_days = [0, 1, 2, 3, 4, 5, 6]
    conn = get_conn()
    token = generate_token()
    c = conn.cursor()
    c.execute(
        '''INSERT INTO students
           (name, token, allowed_days, start_hour, end_hour, daily_limit_minutes)
           VALUES (?,?,?,?,?,?)''',
        (name, token, json.dumps(allowed_days), start_hour, end_hour, daily_limit)
    )
    sid = c.lastrowid
    conn.commit()
    conn.close()
    return sid, token


def _hydrate(row):
    if row is None:
        return None
    s = dict(row)
    s['allowed_days'] = json.loads(s['allowed_days'])
    return s


def get_all_students():
    conn = get_conn()
    rows = conn.execute(
        'SELECT * FROM students WHERE active=1 ORDER BY name'
    ).fetchall()
    conn.close()
    students = [_hydrate(r) for r in rows]
    for s in students:
        s['minutes_today'] = get_watch_minutes_today(s['id'])
    return students


def get_student_by_id(sid):
    conn = get_conn()
    row = conn.execute('SELECT * FROM students WHERE id=?', (sid,)).fetchone()
    conn.close()
    s = _hydrate(row)
    if s:
        s['minutes_today'] = get_watch_minutes_today(s['id'])
    return s


def get_student_by_token(token):
    conn = get_conn()
    row = conn.execute(
        'SELECT * FROM students WHERE token=? AND active=1', (token,)
    ).fetchone()
    conn.close()
    return _hydrate(row)


def update_student(sid, **kwargs):
    conn = get_conn()
    if 'allowed_days' in kwargs and isinstance(kwargs['allowed_days'], list):
        kwargs['allowed_days'] = json.dumps(kwargs['allowed_days'])
    for key, val in kwargs.items():
        if val is not None:
            conn.execute(f'UPDATE students SET {key}=? WHERE id=?', (val, sid))
    conn.commit()
    conn.close()


def toggle_emergency_block(sid, blocked):
    conn = get_conn()
    conn.execute(
        'UPDATE students SET emergency_blocked=? WHERE id=?',
        (1 if blocked else 0, sid)
    )
    conn.commit()
    conn.close()


def block_all_students(blocked):
    conn = get_conn()
    conn.execute('UPDATE students SET emergency_blocked=?', (1 if blocked else 0,))
    conn.commit()
    conn.close()


def delete_student(sid):
    conn = get_conn()
    conn.execute('UPDATE students SET active=0 WHERE id=?', (sid,))
    conn.commit()
    conn.close()


# ── Watch-time tracking ───────────────────────────────────────────────────────

SESSION_GAP_MINUTES = 5   # New session if no query for 5+ min


def record_watch_query(student_id):
    today = date.today().isoformat()
    now   = datetime.now().isoformat()
    conn  = get_conn()

    row = conn.execute(
        '''SELECT id, last_query FROM watch_sessions
           WHERE student_id=? AND date=?
           ORDER BY id DESC LIMIT 1''',
        (student_id, today)
    ).fetchone()

    if row:
        diff = (datetime.now() -
                datetime.fromisoformat(row['last_query'])).total_seconds() / 60
        if diff <= SESSION_GAP_MINUTES:
            conn.execute(
                'UPDATE watch_sessions SET last_query=? WHERE id=?',
                (now, row['id'])
            )
        else:
            conn.execute(
                '''INSERT INTO watch_sessions
                   (student_id, date, session_start, last_query)
                   VALUES (?,?,?,?)''',
                (student_id, today, now, now)
            )
    else:
        conn.execute(
            '''INSERT INTO watch_sessions
               (student_id, date, session_start, last_query)
               VALUES (?,?,?,?)''',
            (student_id, today, now, now)
        )

    conn.commit()
    conn.close()


def get_watch_minutes_today(student_id):
    today = date.today().isoformat()
    conn  = get_conn()
    rows  = conn.execute(
        'SELECT session_start, last_query FROM watch_sessions WHERE student_id=? AND date=?',
        (student_id, today)
    ).fetchall()
    conn.close()

    total = 0.0
    for r in rows:
        start = datetime.fromisoformat(r['session_start'])
        last  = datetime.fromisoformat(r['last_query'])
        total += (last - start).total_seconds() / 60

    return round(total, 1)


# ── Access logs ───────────────────────────────────────────────────────────────

def log_access(student_id, domain, action):
    conn = get_conn()
    conn.execute(
        'INSERT INTO access_logs (student_id, domain, action) VALUES (?,?,?)',
        (student_id, domain, action)
    )
    conn.commit()
    conn.close()


def get_logs(student_id=None, limit=100):
    conn = get_conn()
    if student_id:
        rows = conn.execute(
            '''SELECT l.*, s.name AS student_name
               FROM access_logs l
               JOIN students s ON l.student_id=s.id
               WHERE l.student_id=?
               ORDER BY l.timestamp DESC LIMIT ?''',
            (student_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            '''SELECT l.*, s.name AS student_name
               FROM access_logs l
               JOIN students s ON l.student_id=s.id
               ORDER BY l.timestamp DESC LIMIT ?''',
            (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
