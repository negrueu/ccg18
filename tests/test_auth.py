from datetime import datetime, timedelta, timezone
import pytest
from app import auth, members


def _setup(conn):
    members.upsert(conn, "Ion Popa", "000111222", ["1"])
    members.upsert(conn, "Admin", "000000999", ["2"], is_admin=True)


def test_foreign_contact_rejected(conn):
    _setup(conn)
    assert auth.login_contact(conn, 100, 555, "+37300111222") is None
    assert members.find_by_phone(conn, "000111222")["telegram_id"] is None


def test_missing_contact_user_id_rejected(conn):
    _setup(conn)
    assert auth.login_contact(conn, 100, None, "37300111222") is None


def test_unknown_and_known(conn):
    _setup(conn)
    assert auth.login_contact(conn, 100, 100, "000777777") is None
    m = auth.login_contact(conn, 100, 100, "37300111222")
    assert m["name"] == "Ion Popa"
    assert members.by_telegram(conn, 100)["id"] == m["id"]


def test_admin_token_single_use_and_expiry(conn):
    _setup(conn)
    adm = members.find_by_phone(conn, "000000999")
    t = auth.issue_admin_token(conn, adm["id"])
    assert auth.consume_admin_token(conn, t)["id"] == adm["id"]
    assert auth.consume_admin_token(conn, t) is None
    t2 = auth.issue_admin_token(conn, adm["id"])
    future = datetime.now(timezone.utc) + timedelta(minutes=11)
    assert auth.consume_admin_token(conn, t2, now=future) is None


def _signed(token, fields):
    import hashlib, hmac
    from urllib.parse import urlencode
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


def test_webapp_init_data():
    import time
    tok = "123:ABC"
    now = int(time.time())
    data = _signed(tok, {"auth_date": str(now), "query_id": "q1", "user": '{"id":42,"first_name":"Ion"}'})
    assert auth.verify_webapp_init_data(data, tok)["id"] == 42
    assert auth.verify_webapp_init_data(data, "123:OTHER") is None
    tampered = data.replace("%3A42", "%3A43")
    assert tampered != data and auth.verify_webapp_init_data(tampered, tok) is None
    old = _signed(tok, {"auth_date": str(now - 7200), "user": '{"id":42}'})
    assert auth.verify_webapp_init_data(old, tok) is None


def test_non_admin_no_token(conn):
    _setup(conn)
    with pytest.raises(ValueError):
        auth.issue_admin_token(conn, members.find_by_phone(conn, "000111222")["id"])
