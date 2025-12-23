from __future__ import annotations

import argparse
import json
import os

from .db import Database


def main() -> None:
    p = argparse.ArgumentParser(description="Reset a user's password in the Version Manager SQLite DB")
    p.add_argument("--db", dest="db_path", default=os.path.join("data", "vm.sqlite3"))
    p.add_argument("--username", default="admin")
    p.add_argument("--password", required=True, help="New password (>= 8 chars)")
    args = p.parse_args()

    db = Database(args.db_path)
    try:
        ok = db.update_user_password(username=args.username, new_password=args.password)
        if not ok:
            print(json.dumps({"ok": False, "error": "user_not_found", "username": args.username}, ensure_ascii=False))
            return
        print(json.dumps({"ok": True, "username": args.username, "db_path": os.path.abspath(args.db_path)}, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()

