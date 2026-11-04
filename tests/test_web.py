from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from app import auth, config, db, dues, members, seed, votes
from app.main import create_app


def _client():
    s = config.Settings(bot_token="", secret_key="k" * 40, base_url="http://test", db_path=":memory:",
                        seed_demo=True, admin_phones=["000999888"], run_bot=False, upload_dir="data/test-uploads")
    conn = db.connect(":memory:")
    app = create_app(s, conn)
    return TestClient(app, headers={"Origin": "http://test"}), conn


def _csrf(client, path="/membri"):
    import re
    return re.search(r'name="csrf" value="([0-9a-f]+)"', client.get(path).text).group(1)


def test_auth_and_minutes_have_no_phones():
    client, conn = _client()
    with client:
        assert client.get("/voturi", follow_redirects=False).status_code == 401
        adm = members.find_by_phone(conn, "000999888")
        tok = auth.issue_admin_token(conn, adm["id"])
        assert client.get(f"/login/{tok}", follow_redirects=False).status_code == 303
        assert client.get(f"/login/{tok}", follow_redirects=False).status_code == 401
        assert client.get("/voturi").status_code == 200
        tok = _csrf(client)
        r = client.post("/voturi", data={"csrf": tok, "title": "Buget 2027", "options": "Pentru\nÎmpotrivă",
                                         "weight_mode": "garage", "deadline": "2030-01-01T18:00"},
                        follow_redirects=False)
        assert r.status_code == 303
        vid = int(r.headers["location"].rsplit("/", 1)[1])
        m1 = members.find_by_phone(conn, "000000000")
        votes.cast(conn, vid, m1["id"], 0)
        r = client.post(f"/voturi/{vid}/hartie", data={"csrf": tok, "member_id": members.find_by_phone(conn, "000000001")["id"],
                                                        "option_idx": 1}, follow_redirects=False)
        assert "err" not in r.headers["location"]
        pv = client.get(f"/voturi/{vid}/pv").text
        assert "introdus de Administrator 1" in pv
        for p in conn.execute("select phone from members"):
            assert p[0] not in pv
        for path in ("/panou", "/membri", "/anunturi", "/cotizatii", "/sesizari", f"/voturi/{vid}"):
            assert client.get(path).status_code == 200, path


def test_viber_import_confirm_and_audit():
    client, conn = _client()
    with client:
        adm = members.find_by_phone(conn, "000999888")
        client.get(f"/login/{auth.issue_admin_token(conn, adm['id'])}")
        csv_bytes = "nume_viber,telefon,observatie\nPorecla,+37300777001,\n".encode("utf-8-sig")
        tok = _csrf(client)
        r = client.post("/membri/import", data={"csrf": tok}, files={"file": ("v.csv", csv_bytes, "text/csv")}, follow_redirects=False)
        assert "neconfirmati" in r.headers["location"]
        u = members.find_by_phone(conn, "000777001")
        assert u["status"] == "neconfirmat"
        members.set_self_info(conn, u["id"], "Ion Vecinu", "77, 78")
        assert "Ion Vecinu" in client.get("/membri?f=neconfirmat").text
        client.post(f"/membri/{u['id']}/confirma", data={"csrf": tok, "nume": "Ion Vecinu", "garaje": "77, 78", "rand": "C"})
        u = members.find_by_phone(conn, "000777001")
        assert u["status"] == "confirmat" and [g["number"] for g in members.garages(conn, u["id"])] == ["77", "78"]
        j = client.get("/jurnal").text
        assert "membru_confirmat" in j and "import_membri" in j


def test_webapp_login_rejects_bad_data():
    client, _ = _client()
    with client:
        r = client.post("/api/webapp-login", json={"initData": "user=%7B%22id%22%3A1%7D&hash=abc"})
        assert r.status_code == 401


def test_csrf_and_security_headers():
    client, conn = _client()
    with client:
        adm = members.find_by_phone(conn, "000999888")
        client.get(f"/login/{auth.issue_admin_token(conn, adm['id'])}")
        data = {"nume": "Atacator", "telefon": "000666666", "garaje": "999"}
        # fără token
        assert client.post("/membri/adauga", data=data, follow_redirects=False).status_code == 403
        # token bun, dar altă origine
        tok = _csrf(client)
        r = client.post("/membri/adauga", data={**data, "csrf": tok}, headers={"Origin": "https://evil.example"},
                        follow_redirects=False)
        assert r.status_code == 403
        assert members.find_by_phone(conn, "000666666") is None
        # control pozitiv: aceeași origine + token -> acceptat
        assert client.post("/membri/adauga", data={**data, "csrf": tok}, follow_redirects=False).status_code == 303
        h = client.get("/membri").headers
        assert "frame-ancestors" in h["content-security-policy"] and h["x-content-type-options"] == "nosniff"


def test_weak_secret_refused_and_webhook_closed():
    import pytest
    weak = config.Settings(bot_token="", secret_key="dev-secret", base_url="http://test", db_path=":memory:",
                           seed_demo=False, admin_phones=[], run_bot=False, upload_dir="data/test-uploads")
    with pytest.raises(RuntimeError):
        create_app(weak, db.connect(":memory:"))
    client, _ = _client()
    with client:
        r = client.post("/tg/webhook", json={"update_id": 1, "message": {"text": "/panou"}})
        assert r.status_code == 404


def test_unauthenticated_upload_rejected_before_parsing_and_size_limit():
    client, _ = _client()
    with client:
        r = client.post("/membri/import", files={"file": ("x.csv", b"a" * 1000, "text/csv")})
        assert r.status_code == 401
        r = client.post("/anunturi", content=b"x", headers={"content-length": str(20 * 1024 * 1024)})
        assert r.status_code == 413


def test_secret_vote_hidden_while_open_and_paper_only_without_telegram():
    client, conn = _client()
    with client:
        adm = members.find_by_phone(conn, "000999888")
        client.get(f"/login/{auth.issue_admin_token(conn, adm['id'])}")
        tok = _csrf(client)
        r = client.post("/voturi", data={"csrf": tok, "title": "Presedinte", "options": "A" + chr(10) + "B", "weight_mode": "member",
                                         "secret": "1", "deadline": "2030-01-01T18:00"}, follow_redirects=False)
        vid = int(r.headers["location"].rsplit("/", 1)[1])
        m1 = members.find_by_phone(conn, "000000000")
        votes.cast(conn, vid, m1["id"], 0)
        page = client.get(f"/voturi/{vid}").text
        assert "apar doar după închidere" in page
        assert "după închidere" in client.get(f"/voturi/{vid}/pv").text
        m2 = members.find_by_phone(conn, "000000002")
        members.link_telegram(conn, m2["id"], 555)
        r = client.post(f"/voturi/{vid}/hartie", data={"csrf": tok, "member_id": m2["id"], "option_idx": 1},
                        follow_redirects=False)
        assert "has_telegram" in r.headers["location"]


def test_landing_public_and_qr():
    client, _ = _client()
    with client:
        r = client.get("/")
        assert r.status_code == 200 and "1010600015220" in r.text and "t.me/ccg18bot" in r.text
        assert client.get("/qr-bot.png").content.startswith(bytes([0x89]) + b"PNG")
        assert client.get("/panou", follow_redirects=False).status_code == 401


def _tg_login_params(token, tg_id, auth_date=None):
    import hashlib, hmac, time
    d = {"id": str(tg_id), "first_name": "Ion", "auth_date": str(auth_date or int(time.time()))}
    check = chr(10).join(f"{k}={d[k]}" for k in sorted(d))
    d["hash"] = hmac.new(hashlib.sha256(token.encode()).digest(), check.encode(), hashlib.sha256).hexdigest()
    return d


def test_member_site_telegram_login_and_vote():
    from app import member_web
    tok = "123:SECRET"
    assert member_web.verify_telegram_login(_tg_login_params(tok, 7), tok) == 7
    assert member_web.verify_telegram_login(_tg_login_params(tok, 7), "123:OTHER") is None
    assert member_web.verify_telegram_login(_tg_login_params(tok, 7, auth_date=1), tok) is None
    s = config.Settings(bot_token=tok, secret_key="k" * 40, base_url="http://test", db_path=":memory:",
                        seed_demo=True, admin_phones=[], run_bot=False, upload_dir="data/test-uploads")
    conn = db.connect(":memory:")
    client = TestClient(create_app(s, conn), headers={"Origin": "http://test"})
    with client:
        assert client.get("/cont", follow_redirects=False).headers["location"] == "/cont/intrare"
        # cont Telegram nelegat -> trimis în bot
        assert "unlinked" in client.get("/cont/tg", params=_tg_login_params(tok, 999), follow_redirects=False).headers["location"]
        m = members.find_by_phone(conn, "000000000")
        members.link_telegram(conn, m["id"], 77)
        r = client.get("/cont/tg", params=_tg_login_params(tok, 77), follow_redirects=False)
        assert r.headers["location"] == "/cont"
        v = votes.create(conn, "Buget", "", ["Pentru", "Contra"], "member", False,
                         datetime.now(timezone.utc) + timedelta(days=1))
        page = client.get(f"/cont/vot/{v}").text
        import re
        csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', page).group(1)
        assert "err=confirm" in client.post(f"/cont/vot/{v}", data={"csrf": csrf, "option_idx": 0},
                                            follow_redirects=False).headers["location"]
        client.post(f"/cont/vot/{v}", data={"csrf": csrf, "option_idx": 0, "confirm": "da"})
        assert votes.has_voted(conn, v, m["id"])
        assert client.post(f"/cont/vot/{v}", data={"option_idx": 1, "confirm": "da"},
                           follow_redirects=False).status_code == 403


def test_generate_member_dues_and_member_dashboard():
    import re
    tok = "123:SECRET"
    s = config.Settings(bot_token=tok, secret_key="k" * 40, base_url="http://test", db_path=":memory:",
                        seed_demo=True, admin_phones=["000999888"], run_bot=False, upload_dir="data/test-uploads",
                        pay_beneficiary="CCG TEST", pay_iban="XX00TEST", pay_fiscal="0", pay_bank="Banca Test")
    conn = db.connect(":memory:")
    client = TestClient(create_app(s, conn), headers={"Origin": "http://test"})
    with client:
        adm = members.find_by_phone(conn, "000999888")
        client.get(f"/login/{auth.issue_admin_token(conn, adm['id'])}")
        csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', client.get("/cotizatii").text).group(1)
        client.post("/cotizatii/genereaza", data={"csrf": csrf, "period": "2027", "amount": "1200"})
        m = members.find_by_phone(conn, "000000000")
        assert any(d["garage"] is None and d["period"] == "2027" for d in dues.for_member(conn, m["id"]))
        members.link_telegram(conn, m["id"], 88)
        client.get("/cont/tg", params=_tg_login_params(tok, 88))
        page = client.get("/cont").text
        assert "lei de achitat" in page and "XX00TEST" in page and "Cotizatie 2027 CCG-18" in page
        assert "Garajele mele" in page and "Voturile mele" in page


def test_privacy_policy_16_items_and_links():
    client, _ = _client()
    with client:
        p = client.get("/confidentialitate").text
        for anchor in ("operator", "contact", "dpo", "scopuri", "categorii", "sursa", "destinatari", "transfer",
                       "durata", "drepturi", "consimtamant", "plangere", "obligatie", "automat", "cookie"):
            assert f'id="{anchor}"' in p, anchor
        assert "Legii nr. 195/2024" in p and "datepersonale.md" in p
        assert 'href="/confidentialitate"' in client.get("/").text
        assert 'href="/confidentialitate"' in client.get("/cont/intrare").text


def test_member_miniapp_login():
    tok = "123:SECRET"
    s = config.Settings(bot_token=tok, secret_key="k" * 40, base_url="http://test", db_path=":memory:",
                        seed_demo=True, admin_phones=[], run_bot=False, upload_dir="data/test-uploads")
    conn = db.connect(":memory:")
    client = TestClient(create_app(s, conn), headers={"Origin": "http://test"})
    import time
    import hashlib, hmac
    from urllib.parse import urlencode

    def _signed(token, fields):
        check = chr(10).join(f"{k}={fields[k]}" for k in sorted(fields))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})
    with client:
        good = _signed(tok, {"auth_date": str(int(time.time())), "user": '{"id":501}'})
        assert client.post("/cont/api/webapp-login", json={"initData": good}).status_code == 403  # nelegat
        m = members.find_by_phone(conn, "000000003")
        members.link_telegram(conn, m["id"], 501)
        assert client.post("/cont/api/webapp-login", json={"initData": good}).status_code == 200
        assert "Garajele mele" in client.get("/cont").text
        bad = _signed("999:OTHER", {"auth_date": str(int(time.time())), "user": '{"id":501}'})
        assert client.post("/cont/api/webapp-login", json={"initData": bad}).status_code == 401
