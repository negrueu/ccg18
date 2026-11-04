import io

import qrcode


def add(conn, garage_id, period, amount):
    conn.execute("insert or ignore into dues(garage_id, period, amount) values(?,?,?)", (garage_id, period, amount))


def add_member(conn, member_id, period, amount):
    conn.execute("insert or ignore into dues(member_id, period, amount) values(?,?,?)", (member_id, period, amount))


def generate_member_dues(conn, period, amount) -> int:
    """Cotizație pe membru (ex. CCG 18: 1.200 lei/an) pentru toți membrii confirmați. Idempotent."""
    before = conn.execute("select count(*) from dues where member_id is not null and period=?", (period,)).fetchone()[0]
    for m in conn.execute("select id from members where status='confirmat'").fetchall():
        add_member(conn, m["id"], period, amount)
    after = conn.execute("select count(*) from dues where member_id is not null and period=?", (period,)).fetchone()[0]
    return after - before


def for_member(conn, member_id):
    rows = conn.execute(
        "select d.id, g.number as garage, d.period, d.amount, d.paid_at is not null as paid, d.paid_at "
        "from dues d join garages g on g.id=d.garage_id where g.member_id=? "
        "union all "
        "select d.id, null as garage, d.period, d.amount, d.paid_at is not null as paid, d.paid_at "
        "from dues d where d.member_id=? order by period, garage", (member_id, member_id)).fetchall()
    return [dict(r) for r in rows]


def list_all(conn):
    return conn.execute(
        "select d.*, g.number as garage, coalesce(m1.name, m2.name) as name from dues d "
        "left join garages g on g.id=d.garage_id left join members m1 on m1.id=g.member_id "
        "left join members m2 on m2.id=d.member_id "
        "order by d.paid_at is not null, d.period desc, coalesce(m1.name, m2.name)").fetchall()


def mark_paid(conn, due_id):
    conn.execute("update dues set paid_at=coalesce(paid_at, datetime('now')) where id=?", (due_id,))


def summary(conn):
    r = conn.execute("select coalesce(sum(case when paid_at is null then amount end),0) datorat, "
                     "coalesce(sum(case when paid_at is not null then amount end),0) incasat from dues").fetchone()
    return dict(r)


def payment_purpose(period, name, garages) -> str:
    g = f", garaj {garages}" if garages and garages != "-" else ""
    return f"Cotizatie {period} CCG-18, {name}{g}"


def qr_png(text: str) -> bytes:
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
