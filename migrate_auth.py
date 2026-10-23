"""Add only authentication tables, with an exclusive-name consistent DB backup."""
import argparse
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import sqlite3

from auth_security import init_auth_schema


def migrate(database, backup):
    database, backup = Path(database).resolve(), Path(backup).resolve()
    if not database.is_file() or database == backup:
        raise ValueError('Choose an existing database and a different new backup path.')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as source:
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'posts', 'reader_users', 'settings'} <= tables:
            raise ValueError('Not an initialized Orange reader database.')
        # Never overwrite a previous backup, even if another process races us.
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(backup)) as dest:
            source.backup(dest)
    with closing(sqlite3.connect(database.as_uri()+'?mode=rw', uri=True, timeout=10)) as db:
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            db.execute('BEGIN IMMEDIATE')
            init_auth_schema(db)
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Database integrity check failed; migration rolled back.')
            if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
                raise RuntimeError('Foreign key check failed; migration rolled back.')
    return backup


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', required=True)
    p.add_argument('--backup')
    args=p.parse_args()
    dbpath=Path(args.database)
    backup=args.backup or dbpath.with_name(dbpath.name+'.before-auth-'+datetime.now().strftime('%Y%m%dT%H%M%S%f'))
    try:
        result=migrate(dbpath, backup)
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as error:
        p.exit(1, 'Migration failed: '+str(error)+'\n')
    print('Authentication tables ready; consistent backup:',result)
