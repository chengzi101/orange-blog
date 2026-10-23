"""Idempotent additive migration with an online SQLite backup."""
import argparse
import sqlite3
import os
from pathlib import Path
from datetime import datetime
from community import init_community

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--backup')
    args = parser.parse_args()
    path = Path(args.database).resolve()
    if not path.is_file():
        parser.error('Existing database not found; use app.py to initialize a new local project.')
    backup = Path(args.backup) if args.backup else path.with_name(path.name + '.before-community-' + datetime.now().strftime('%Y%m%dT%H%M%S'))
    if backup.exists():
        parser.error('Backup already exists; choose a new backup path.')
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON')
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(backup) as copy:
            db.backup(copy)
        with db:
            db.execute('BEGIN IMMEDIATE')
            articles_before = db.execute('SELECT * FROM posts ORDER BY id').fetchall()
            init_community(db)
            if articles_before != db.execute('SELECT * FROM posts ORDER BY id').fetchall():
                raise RuntimeError('Article data changed during schema migration; transaction rolled back')
    print('Migration complete; existing articles preserved. Backup:', backup)
