# AWS Lightsail Deployment Notes

## Infrastructure

- **Host:** AWS Lightsail (Ubuntu 22.04)
- **WSGI Server:** Gunicorn 25.1.0
- **Reverse Proxy:** Nginx
- **Database:** PostgreSQL (via `DATABASE_URL` env var)
- **HTTPS:** Already configured (required for Stripe live payments)
- **Service name:** `clips` (managed by systemctl)

---

## Deploy Process

After pulling changes to the server:

```bash
bash scripts/deploy.sh
```

`scripts/deploy.sh` does:
1. `venv/bin/pip install --upgrade pip`
2. `venv/bin/pip install -r requirements.txt` (all versions pinned)
3. `sudo systemctl restart clips`

Before the first deploy that includes the `APP_ENV` startup checks, confirm the
service really has `SECRET_KEY` and `DATABASE_URL`:

```bash
sudo systemctl show clips -p Environment -p EnvironmentFiles
```

If either is missing the app now refuses to start (by design) instead of
silently running against an empty local SQLite file.

### System packages (WeasyPrint)

`pip install weasyprint` succeeds without these, but PDF rendering fails at
runtime until they are present:

```bash
sudo apt install -y libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core
```

---

## Secrets and Environment Variables

Keep every secret in a root-owned file that only the service can read — **not**
inline in the unit file (unit files are world-readable by default):

```bash
sudo install -d -m 750 -o root -g ubuntu /etc/clips
sudo install -m 640 -o root -g ubuntu /dev/null /etc/clips/env
sudo nano /etc/clips/env        # KEY=value, one per line, no quotes needed
```

Then in `/etc/systemd/system/clips.service`, replace the `Environment=` lines with:

```ini
[Service]
EnvironmentFile=/etc/clips/env
```

and `sudo systemctl daemon-reload && sudo systemctl restart clips`.

To run a one-off script with the same environment:

```bash
cd /home/ubuntu/clips
set -a; . /etc/clips/env; set +a
venv/bin/python scripts/init_db.py        # add --force to wipe and re-create
```

### Variables

| Variable | Required in production | Purpose |
|----------|:---:|---------|
| `SECRET_KEY` | **yes** | Session/token signing. Generate: `python -c "import secrets; print(secrets.token_hex(32))"`. Startup fails without it. |
| `DATABASE_URL` | **yes** | PostgreSQL connection string. `postgres://` is auto-corrected to `postgresql://`. Startup fails without it. |
| `APP_ENV` | no | `production` (default) or `development`. Never set `development` on the server. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | first boot only | Creates the first admin when the users table is empty. Remove `ADMIN_PASSWORD` from the file after the first login and change the password at `/settings/account`. |
| `SESSION_COOKIE_SECURE` | no | Defaults to `true` in production. Only set `false` on a plain-HTTP test box. |
| `BASE_URL` | yes | `https://<domain>/` — used by background email threads to build links. |
| `MARKETING_URL` | no | Linked in invoice emails. |
| `QUOTE_INBOX` | yes | Email address that receives quote form submissions. |
| `MAIL_USERNAME` / `MAIL_PASSWORD` / `MAIL_DEFAULT_SENDER` | if not using Gmail OAuth | SMTP fallback. Use a Gmail App Password, never the account password. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `FERNET_KEY` | for Gmail OAuth | `FERNET_KEY` encrypts the stored refresh token: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `STRIPE_SECRET_KEY` / `STRIPE_PUBLISHABLE_KEY` / `STRIPE_WEBHOOK_SECRET` | for online payments | Stripe live keys and webhook signing secret. |
| `PAYMENT_NAME` | yes | Name printed on invoices (check payee, header). |
| `VENMO_HANDLE` | yes | Venmo handle shown on invoices. |
| `CASHAPP_HANDLE` | yes | Cash App handle shown on invoices. |
| `ZELLE_PHONE` | yes | Zelle phone number shown on invoices. |
| `BUSINESS_PHONE` | yes | Business phone on invoices and landing page. |
| `BUSINESS_STREET` | yes | Street address on printed invoices. |
| `BUSINESS_CITY_STATE_ZIP` | yes | City/state/ZIP on printed invoices. |

### GitHub access on the server

Do not clone with a personal access token in the URL — it is stored in
plaintext in `.git/config`. Use a read-only deploy key instead:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/clips_deploy -N ""
cat ~/.ssh/clips_deploy.pub     # add as a Deploy Key on the GitHub repo (read-only)
git remote set-url origin git@github.com:<user>/<repo>.git
```

### Rotation checklist (do once after moving secrets into `/etc/clips/env`)

- [ ] Revoke the Gmail App Password that was in the unit file; create a new one.
- [ ] Revoke the GitHub PAT used for the original clone; switch to the deploy key above.
- [ ] Generate a fresh `SECRET_KEY` (this signs everyone out once; portal/staff setup links older than the rotation stop working — that is expected).
- [ ] Change the admin password at `/settings/account`.
- [ ] `sudo chmod 644 /etc/systemd/system/clips.service` is fine again once it contains no secrets.

---

## Nginx headers the app depends on

Flask sits behind `ProxyFix`, which trusts one hop. The proxy block must send:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
```

Without `X-Forwarded-Proto`, links built inside a request (`url_for(..., _external=True)`)
come out as `http://`. Without `X-Forwarded-For`, the login rate limit applies to
everyone at once.

---

## Database

- No `DATABASE_URL` env var → SQLite (`instance/clips.db`) — **development only**; production refuses to start without it.
- With `DATABASE_URL` set → PostgreSQL — production on Lightsail.

### Initialize / Reset

```bash
set -a; . /etc/clips/env; set +a
venv/bin/python scripts/init_db.py            # fresh DB: schema + admin + expense categories
venv/bin/python scripts/init_db.py --force    # DROPS EVERYTHING and re-creates
```

`scripts/init_db.py` requires `ADMIN_PASSWORD` (12+ characters) in the environment and
refuses to run against a database that already holds business data unless
`--force` is given. `scripts/clear_db.py` is a local-dev tool and refuses non-SQLite
targets.

---

## Common Commands (on the server)

```bash
# Restart the app service
sudo systemctl restart clips

# View live logs
sudo journalctl -u clips -f

# Check service status
sudo systemctl status clips

# Confirm what environment the service actually sees
sudo systemctl show clips -p Environment -p EnvironmentFiles
```

---

## Admin Login

Production has no default password. The first admin is created from
`ADMIN_USERNAME` / `ADMIN_PASSWORD` on first boot (or by `scripts/init_db.py`); change it
afterwards at `/settings/account`. Local development still uses `admin` / `clips2026`.
