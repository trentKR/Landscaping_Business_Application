"""Small request/security helpers shared by the admin, portal, and staff blueprints."""

import hashlib
import hmac
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from urllib.parse import urlparse

from itsdangerous import URLSafeTimedSerializer

# Admin is the most privileged account and is protected by a password alone,
# so it gets a longer floor than the 8-character portal/staff minimum.
MIN_ADMIN_PASSWORD_LENGTH = 12

# Ceiling for any single typed amount. Far above any real entry for this
# business; it exists to turn a fat-fingered "1e309" into an error message.
MAX_AMOUNT = 1_000_000
MAX_HOURS = 24


def parse_amount(raw, maximum: float = MAX_AMOUNT) -> float:
    """Parse a form value into a float rounded half-up to two decimal places.

    Raises ValueError for blank or non-numeric input, NaN, infinity, and
    anything whose magnitude exceeds ``maximum``. Plain ``float()`` accepts
    "nan" and "inf", and NaN slips past every ``amount <= 0`` guard. Postgres
    stores both happily, so one bad entry would poison every balance that sums
    over it. Sign and zero checks stay with the caller, which owns the message.
    """
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        raise ValueError(f'not a number: {raw!r}') from None
    if not value.is_finite() or abs(value) > maximum:
        raise ValueError(f'amount out of range: {raw!r}')
    return float(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def is_safe_redirect_target(target: str | None) -> bool:
    """True only for a same-site path like ``/portal/invoices/3``.

    Rejects absolute URLs, protocol-relative URLs (``//evil.example``), and the
    backslash variant some browsers normalise to ``//``.
    """
    if not target:
        return False
    if not target.startswith('/') or target.startswith(('//', '/\\')):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def portal_token_nonce(password_hash: str | None) -> str:
    """Short digest of the current password hash.

    Embedded in every password-setup token so that a link stops working the
    moment the account's password is set or changed. Accounts with no password
    yet share the empty-hash nonce, which is fine: until a password exists,
    every outstanding link is meant to be equivalent.
    """
    return hashlib.sha256((password_hash or '').encode()).hexdigest()[:16]


def make_setup_token(secret_key: str, id_field: str, record) -> str:
    """Signed, time-limited token for a portal/staff password-setup link.

    ``id_field`` is ``'client_id'`` or ``'employee_id'``; ``record`` must have
    ``id`` and ``password_hash`` attributes.
    """
    serializer = URLSafeTimedSerializer(secret_key)
    return serializer.dumps({id_field: record.id,
                             'n': portal_token_nonce(record.password_hash)})


def setup_token_matches(data: dict, record) -> bool:
    """Check the nonce inside a loaded setup token against the record's current state."""
    expected = portal_token_nonce(record.password_hash)
    return hmac.compare_digest(str(data.get('n', '')), expected)
