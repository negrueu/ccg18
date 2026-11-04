from app import members, notices, dues, reports


def _people(conn):
    a = members.upsert(conn, "A", "000000001", ["1", "2"], "A")
    b = members.upsert(conn, "B", "000000002", ["10"], "B")
    c = members.upsert(conn, "C", "000000003", ["11"], "B")
    members.link_telegram(conn, a, 101)
    members.link_telegram(conn, b, 102)
    return a, b, c


def test_notice_row_filter(conn):
    a, b, c = _people(conn)
    nid = notices.create(conn, "anunt", "Apa", "Se taie apa", row_label="B")
    assert [r["id"] for r in notices.recipients(conn, nid)] == [b]
    nid2 = notices.create(conn, "anunt", "Toti", "")
    assert sorted(r["id"] for r in notices.recipients(conn, nid2)) == sorted([a, b])


def test_ack_idempotent(conn):
    a, b, _ = _people(conn)
    nid = notices.create(conn, "convocare", "Adunare", "")
    notices.mark_delivered(conn, nid, a)
    notices.mark_delivered(conn, nid, b)
    notices.ack(conn, nid, a)
    notices.ack(conn, nid, a)
    s = notices.stats(conn, nid)
    assert s == {"delivered": 2, "acked": 1, "total": 2}
    # ack fără livrare (callback_data falsificat) nu contează
    nid2 = notices.create(conn, "convocare", "Rand D", "", row_label="D")
    notices.ack(conn, nid2, a)
    assert notices.stats(conn, nid2)["acked"] == 0


def test_dues_and_qr(conn):
    a, _, _ = _people(conn)
    for g in members.garages(conn, a):
        dues.add(conn, g["id"], "2026", 650)
    rows = dues.for_member(conn, a)
    assert [r["garage"] for r in rows] == ["1", "2"]
    assert sum(r["amount"] for r in rows if not r["paid"]) == 1300
    dues.mark_paid(conn, rows[0]["id"])
    assert sum(r["amount"] for r in dues.for_member(conn, a) if not r["paid"]) == 650
    assert dues.qr_png("x").startswith(bytes([0x89]) + b"PNG")


def test_member_dues(conn):
    a, b, c = _people(conn)
    conn.execute("update members set status='neconfirmat' where id=?", (c,))
    assert dues.generate_member_dues(conn, "2026", 1200) == 2
    assert dues.generate_member_dues(conn, "2026", 1200) == 0
    rows = dues.for_member(conn, a)
    assert any(r["garage"] is None and r["amount"] == 1200 for r in rows)
    assert dues.for_member(conn, c) == []
    assert "garaj 1, 2" in dues.payment_purpose("2026", "A", "1, 2")


def test_reports(conn):
    a, _, _ = _people(conn)
    rid = reports.create(conn, a, "Bec ars rand 4", "file123")
    assert reports.list_all(conn)[0]["status"] == "nou"
    m = reports.resolve(conn, rid)
    assert m["telegram_id"] == 101
    assert reports.list_all(conn)[0]["status"] == "rezolvat"
