"""
Simple migration script: copies data from local SQLite `DB_PATH` to Postgres using
SQLAlchemy. Usage:

DATABASE_URL=postgres://user:pass@host:5432/dbname python migrate_sqlite_to_postgres.py

If `DB_PATH` is set it will use that SQLite file; otherwise 'guardian.db'.
"""
import os
import sqlite3
from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, Text,
                        String, Boolean, DateTime)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import JSONB

DATABASE_URL = os.environ.get('DATABASE_URL')
DB_PATH = os.environ.get('DB_PATH', 'guardian.db')

if not DATABASE_URL:
    print('Error: set DATABASE_URL environment variable to target Postgres DB')
    exit(1)

# Connect to Postgres
engine = create_engine(DATABASE_URL)
metadata = MetaData()

# Define tables matching the SQLite schema
students = Table('students', metadata,
    Column('id', Integer, primary_key=True),
    Column('name', Text, nullable=False),
    Column('token', Text, nullable=False),
    Column('allowed_days', Text),
    Column('start_hour', Integer),
    Column('end_hour', Integer),
    Column('daily_limit_minutes', Integer),
    Column('active', Integer),
    Column('emergency_blocked', Integer),
    Column('created_at', DateTime),
)

access_logs = Table('access_logs', metadata,
    Column('id', Integer, primary_key=True),
    Column('student_id', Integer, nullable=False),
    Column('domain', Text, nullable=False),
    Column('action', Text, nullable=False),
    Column('timestamp', DateTime),
)

watch_sessions = Table('watch_sessions', metadata,
    Column('id', Integer, primary_key=True),
    Column('student_id', Integer, nullable=False),
    Column('date', Text, nullable=False),
    Column('session_start', Text, nullable=False),
    Column('last_query', Text, nullable=False),
)

try:
    metadata.create_all(engine)
    print('Created tables in Postgres')
except SQLAlchemyError as e:
    print('Error creating tables:', e)
    exit(1)

# Read from SQLite and insert into Postgres
conn_sqlite = sqlite3.connect(DB_PATH)
conn_sqlite.row_factory = sqlite3.Row
cur = conn_sqlite.cursor()

with engine.begin() as conn_pg:
    # Students
    cur.execute('SELECT * FROM students')
    rows = cur.fetchall()
    if rows:
        conn_pg.execute(students.insert(), [dict(r) for r in rows])
        print(f'Copied {len(rows)} students')

    # Access logs
    cur.execute('SELECT * FROM access_logs')
    rows = cur.fetchall()
    if rows:
        conn_pg.execute(access_logs.insert(), [dict(r) for r in rows])
        print(f'Copied {len(rows)} access_logs')

    # Watch sessions
    cur.execute('SELECT * FROM watch_sessions')
    rows = cur.fetchall()
    if rows:
        conn_pg.execute(watch_sessions.insert(), [dict(r) for r in rows])
        print(f'Copied {len(rows)} watch_sessions')

print('Migration complete')
