def create(conn, kind, title, body, row_label=None, file_path=None) -> int:
    if kind not in ("anunt", "convocare"):
        raise ValueError("kind")
    return conn.execute(
        "insert into notices(kind, title, body, row_label, file_path) values(?,?,?,?,?)",
        (kind, title, body, row_label or None, file_path)).lastrowid


def get(conn, nid):
    return conn.execute("select * from notices where id=?", (nid,)).fetchone()


def list_all(conn, limit=50):
    return conn.execute("select * from notices order by id desc limit ?", (limit,)).fetchall()


def recipients(conn, nid):
    n = get(conn, nid)
    if n["row_label"]:
        return conn.execute(
            "select distinct m.* from members m join garages g on g.member_id=m.id "
            "where m.telegram_id is not null and g.row_label=? order by m.id", (n["row_label"],)).fetchall()
    return conn.execute("select * from members where telegram_id is not null order by id").fetchall()


def mark_delivered(conn, nid, mid):
    conn.execute("insert or ignore into notice_delivery(notice_id, member_id) values(?,?)", (nid, mid))


def ack(conn, nid, mid):
    """Confirmarea se acceptă doar dacă mesajul i-a fost livrat acestui membru."""
    conn.execute("update notice_delivery set ack_at=coalesce(ack_at, datetime('now')) "
                 "where notice_id=? and member_id=?", (nid, mid))


def stats(conn, nid) -> dict:
    r = conn.execute("select count(*) d, count(ack_at) a from notice_delivery where notice_id=?", (nid,)).fetchone()
    return {"delivered": r["d"], "acked": r["a"], "total": len(recipients(conn, nid))}


def recent_for(conn, mid, limit=5):
    return conn.execute(
        "select n.* from notices n join notice_delivery d on d.notice_id=n.id where d.member_id=? "
        "order by n.id desc limit ?", (mid, limit)).fetchall()
