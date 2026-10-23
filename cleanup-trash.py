"""Server housekeeping: remove items retained in trash for 30 full days."""
import argparse
import json
from app import app, purge_expired_trash

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    with app.app_context():
        result = purge_expired_trash(dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1 if result['failed'] else 0)
