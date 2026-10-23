"""Trusted local/SSH operations. Run with the service environment; never expose as HTTP."""
import argparse
import json
import os
import time


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['issue-ticket','reset-login-limits','revoke-admin-sessions','cleanup'])
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--batch-size',type=int,default=500)
    p.add_argument('--max-batches',type=int,default=60)
    args=p.parse_args()
    if args.action!='cleanup' and (args.dry_run or args.batch_size!=500 or args.max_batches!=60):
        p.error('Cleanup options are only valid for cleanup')
    if not 1<=args.batch_size<=500 or not 1<=args.max_batches<=60:
        p.error('Use batch-size 1..500 and max-batches 1..60')
    if not os.environ.get('ORANGE_SECRET_KEY') or not os.environ.get('ORANGE_DB'):
        p.exit(1, 'Explicit ORANGE_SECRET_KEY and ORANGE_DB from the service environment are required.\n')
    import app as m
    from auth_security import issue_admin_ticket
    if not m.DB_PATH.is_file() or not m.ADMIN_PASSWORD_HASH:
        p.exit(1, 'Existing database and configured administrator password hash are required.\n')
    with m.app.app_context():
        db=m.get_db()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_sessions'").fetchone() is None:
            p.exit(1, 'Run migrate_auth.py before using authentication tools.\n')
        if args.action=='issue-ticket':
            # Credential output is for a private pipe to the launcher. Do not log.
            result={'ticket':issue_admin_ticket(m.app,db),'expires_in':60,'consume_path':'/admin/quick-login'}
        elif args.action=='cleanup':
            from auth_maintenance import cleanup_auth_metadata
            result=cleanup_auth_metadata(db,dry_run=args.dry_run,batch_size=args.batch_size,max_batches=args.max_batches)
        else:
            now=int(time.time())
            with db:
                db.execute('BEGIN IMMEDIATE')
                if args.action=='reset-login-limits':
                    count=db.execute("DELETE FROM auth_rate_limits WHERE rate_key LIKE 'admin-login:%'").rowcount
                elif args.action=='revoke-admin-sessions':
                    count=db.execute("UPDATE auth_sessions SET revoked_at=? WHERE admin_stamp!='' AND revoked_at IS NULL", (now,)).rowcount
                    db.execute('DELETE FROM admin_login_tickets')
            result={'ok':True,'affected':count}
    print(json.dumps(result))
    if result.get('stopped'):
        p.exit(75,'Metadata cleanup deferred; see the non-sensitive status above.\n')


if __name__=='__main__':
    main()
