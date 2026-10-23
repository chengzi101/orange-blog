"""Bounded removal of disposable, expired auth metadata; never business rows."""
import sqlite3
import time


def cleanup_auth_metadata(db, *, now=None, batch_size=500, max_batches=60, dry_run=False):
    if not 1 <= batch_size <= 500 or not 1 <= max_batches <= 60:
        raise ValueError('Cleanup batch bounds exceeded')
    cutoff = (int(time.time()) if now is None else int(now)) - 86400
    # Absolute expiry is conservative: revoked sessions also age out by this
    # existing indexed deadline. Valid sessions and current budgets stay intact.
    jobs = (
        ('auth_sessions','session_hash','absolute_expires_at'),
        ('admin_login_tickets','token_hash','expires_at'),
        ('auth_rate_limits','rate_key','window_start'),
    )
    counts = {table:0 for table,_,_ in jobs}
    statements = 0
    stopped = None
    empty = 0
    old_timeout = db.execute('PRAGMA busy_timeout').fetchone()[0]
    db.execute('PRAGMA busy_timeout=250')
    ticks = 0
    def progress():
        nonlocal ticks
        ticks += 1
        return ticks > 100
    try:
        db.set_progress_handler(progress,10000)
        for i in range(max_batches):
            table,key,expires = jobs[i % len(jobs)]
            ticks = 0
            try:
                if dry_run:
                    rows=db.execute(f'SELECT {key} FROM {table} WHERE {expires}<? LIMIT ?', (cutoff,batch_size)).fetchall()
                    counts[table]=len(rows)
                    if i == len(jobs)-1:break
                    continue
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    changed=db.execute(f'DELETE FROM {table} WHERE {key} IN '
                                       f'(SELECT {key} FROM {table} WHERE {expires}<? LIMIT ?)',
                                       (cutoff,batch_size)).rowcount
                statements += 1
                counts[table] += changed
                empty = empty+1 if not changed else 0
                if empty >= len(jobs):break
                # Give foreground writers an opportunity between small commits.
                time.sleep(.01)
            except sqlite3.OperationalError as error:
                message=str(error).lower()
                if 'locked' in message or 'busy' in message:stopped='database_busy'
                elif 'interrupted' in message:stopped='work_budget'
                else:raise
                break
    finally:
        db.set_progress_handler(None,0)
        db.execute(f'PRAGMA busy_timeout={int(old_timeout)}')
    return {'ok':stopped is None,'affected':0 if dry_run else sum(counts.values()),
            'counts':{table:0 for table in counts} if dry_run else counts,
            'eligible_sample':counts if dry_run else {},
            'batches':statements,'dry_run':dry_run,'stopped':stopped}
