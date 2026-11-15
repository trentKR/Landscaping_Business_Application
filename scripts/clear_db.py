"""Reset the local development database to a clean slate.

Drops all tables, recreates the schema, and creates only the admin user
(ADMIN_USERNAME / ADMIN_PASSWORD, defaulting to admin / clips2026 in dev).
No mock data is seeded.

Dev-only convenience. To initialize or reset a server, use
`python scripts/init_db.py --force` with the production environment loaded.

Usage:
  python scripts/clear_db.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

os.environ.setdefault('APP_ENV', 'development')

# Guard before importing the app: importing it connects to the database.
_target = os.environ.get('DATABASE_URL', 'sqlite:///clips.db')
if not _target.startswith('sqlite') and '--yes' not in sys.argv[1:]:
    print(f'Refusing to wipe a non-SQLite database ({_target.split("@")[-1]}). '
          'Pass --yes if you really mean it, or use scripts/init_db.py --force.')
    sys.exit(1)

from app import app  # noqa: E402
from models import User, db  # noqa: E402


def clear() -> None:
    with app.app_context():
        db.drop_all()
        db.create_all()

        username = app.config['ADMIN_USERNAME']
        admin = User(username=username, name='Admin')
        admin.set_password(app.config['ADMIN_PASSWORD'])
        db.session.add(admin)
        db.session.commit()

        shown = '(ADMIN_PASSWORD)' if os.environ.get('ADMIN_PASSWORD') else 'clips2026'
        print(f'Done. Database cleared. Login: {username} / {shown}')


if __name__ == '__main__':
    clear()
