"""Offline migration/restore CLI. Both writers must be stopped for restore."""
import argparse, sqlite3, pathlib, hashlib, json, time, os
from unified_schema import migrate

def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def fingerprint(db):
    result = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        if name == 'unified_migrations': continue  # Migration metadata changes on each schema upgrade.
        # Added columns are excluded: all original user fields remain byte-for-byte.
        cols = [r[1] for r in db.execute('PRAGMA table_info("'+name+'")')]
        if name == 'reader_users': cols = [c for c in cols if c not in ('phone','phone_verified_at','account_status','phone_change_used')]
        rows = [tuple(r) for r in db.execute('SELECT '+','.join('"'+c+'"' for c in cols)+' FROM "'+name+'" ORDER BY rowid')]
        result[name] = hashlib.sha256(repr(rows).encode()).hexdigest()
    return result

def backup(db, path):
    path=pathlib.Path(path)
    if path.exists(): raise RuntimeError('Backup destination already exists')
    target=connect(path)
    try: db.backup(target)
    finally: target.close()
    os.chmod(path,0o600)

def apply(path, backup_path):
    if not pathlib.Path(path).is_file(): raise RuntimeError('Existing database required')
    db=connect(path)
    try:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        before=fingerprint(db); backup(db,backup_path)
        db.execute('PRAGMA journal_mode=WAL')
        with db:
            db.execute('BEGIN IMMEDIATE'); migrate(db)
            after=fingerprint(db)
            assert all(after[k]==v for k,v in before.items()), 'Historical data changed'
            assert not db.execute('PRAGMA foreign_key_check').fetchall()
        return {'ok':True,'backup':str(backup_path),'preserved_tables':list(before)}
    finally: db.close()

def restore(path, snapshot, preserve_path):
    if not pathlib.Path(path).is_file() or not pathlib.Path(snapshot).is_file(): raise RuntimeError('Existing database and backup required')
    current=connect(path); source=sqlite3.connect(pathlib.Path(snapshot).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        assert source.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not source.execute('PRAGMA foreign_key_check').fetchall()
        assert source.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('reader_users','posts')").fetchone()[0]==2,'Not an Orange Blog backup'
        backup(current,preserve_path)
        source.backup(current)
        assert current.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not current.execute('PRAGMA foreign_key_check').fetchall()
    finally: current.close(); source.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['apply','restore']);parser.add_argument('--db',required=True)
    parser.add_argument('--backup',required=True);parser.add_argument('--preserve-current')
    parser.add_argument('--writers-stopped',action='store_true')
    args=parser.parse_args()
    if not args.writers_stopped: parser.error('Stop both writing services and pass --writers-stopped')
    if args.action=='apply': print(json.dumps(apply(args.db,args.backup)))
    else:
        if not args.preserve_current: parser.error('--preserve-current required for restore')
        restore(args.db,args.backup,args.preserve_current); print('{"ok":true,"restored":true}')
