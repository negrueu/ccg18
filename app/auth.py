import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl
from . import members

TOKEN_TTL = timedelta(minutes=10)
WEBAPP_MAX_AGE = 3600  # secunde


def verify_webapp_init_data(init_data: str, bot_token: str, now=None, max_age=WEBAPP_MAX_AGE):
    """Validează Telegram.WebApp.initData (HMAC-SHA256, cheia derivată din "WebAppData").
    Întoarce dict-ul `user` sau None."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop("hash", None)
    if not received or not bot_token:
        return None
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if (now or time.time()) - auth_date > max_age:
        return None
    try:
        return json.loads(pairs.get("user", "null"))
    except ValueError:
        return None


class RelinkBlocked(Exception):
    """Numărul e deja legat de ALT cont Telegram: mutarea cere aprobarea conducerii."""

    def __init__(self, member):
        super().__init__("relink")
        self.member = member


def login_contact(conn, from_user_id: int, contact_user_id, phone: str):
    """Leagă contul Telegram de membru DOAR dacă e contactul propriu al expeditorului și e pe listă.
    Un număr deja legat de alt cont nu se mută automat (SIM duplicat, număr realocat): RelinkBlocked."""
    if contact_user_id is None or contact_user_id != from_user_id:
        return None
    m = members.find_by_phone(conn, phone)
    if not m:
        return None
    if m["telegram_id"] and m["telegram_id"] != from_user_id:
        conn.execute("insert into audit(actor_id, action, detail) values(null, 'relegare_blocata', ?)",
                     (f"membru #{m['id']}: cont Telegram nou {from_user_id} (vechi {m['telegram_id']})",))
        kv_put_pending(conn, m["id"], from_user_id)
        raise RelinkBlocked(m)
    members.link_telegram(conn, m["id"], from_user_id)
    return members.get(conn, m["id"])


def kv_put_pending(conn, member_id, new_tg):
    from . import settings_store as kv
    kv.put(conn, f"relink:{member_id}", new_tg)


def approve_relink(conn, member_id):
    """Conducerea aprobă mutarea pe contul nou (după ce a verificat omul la telefon sau în persoană)."""
    from . import settings_store as kv
    new_tg = kv.get(conn, f"relink:{member_id}")
    if not new_tg:
        return None
    members.link_telegram(conn, member_id, int(new_tg))
    kv.put(conn, f"relink:{member_id}", None)
    return int(new_tg)


def issue_admin_token(conn, member_id: int) -> str:
    m = members.get(conn, member_id)
    if not m or not m["is_admin"]:
        raise ValueError("not admin")
    tok = secrets.token_urlsafe(24)
    exp = (datetime.now(timezone.utc) + TOKEN_TTL).isoformat()
    conn.execute("insert into admin_tokens(token, member_id, expires_at) values(?,?,?)", (tok, member_id, exp))
    return tok


def consume_admin_token(conn, token: str, now=None):
    now = now or datetime.now(timezone.utc)
    row = conn.execute("delete from admin_tokens where token=? returning member_id, expires_at", (token,)).fetchone()
    if not row or datetime.fromisoformat(row["expires_at"]) < now:
        return None
    m = members.get(conn, row["member_id"])
    return m if m and m["is_admin"] else None
