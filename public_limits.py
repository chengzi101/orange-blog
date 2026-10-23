"""Shared request budgets using the existing authentication rate table."""
import time

from auth_security import keyed_digest, LoginLimited


def public_entries(app, kind, dimensions):
    return [('public:'+kind+':'+scope+':'+keyed_digest(app.secret_key,str(value)),limit,window)
            for scope,value,limit,window in dimensions]


def check_entries(db, kind, entries, now):
    writes=[]
    for key,limit,window in entries:
        row=db.execute('SELECT window_start,attempts FROM auth_rate_limits WHERE rate_key=?',(key,)).fetchone()
        active=row is not None and row['window_start']>now-window
        if active and row['attempts']>=limit:
            raise LoginLimited(max(1,row['window_start']+window-now),kind)
        writes.append((key,row['window_start'] if active else now,row['attempts']+1 if active else 1))
    return writes


def check_public(app, db, kind, dimensions):
    # An early rejection only; reservation below remains the atomic authority.
    check_entries(db,kind,public_entries(app,kind,dimensions),int(time.time()))


def reserve_public(app, db, kind, dimensions):
    now=int(time.time())
    entries=public_entries(app,kind,dimensions)
    # Already blocked requests do not acquire SQLite's writer lock or update rows.
    check_entries(db,kind,entries,now)
    with db:
        db.execute('BEGIN IMMEDIATE')
        writes=check_entries(db,kind,entries,now)
        db.executemany('INSERT INTO auth_rate_limits VALUES(?,?,?) ON CONFLICT(rate_key) DO UPDATE SET '
                       'window_start=excluded.window_start,attempts=excluded.attempts',writes)
