import io
import qrcode


def add(conn, garage_id, period, amount):
    conn.execute("insert or ignore into dues(garage_id, period, amount) values(?,?,?)", (garage_id, period, amount))


def for_member(conn, member_id):
    return [dict(r) for r in conn.execute(
        "select d.id, g.number as garage, d.period, d.amount, d.paid_at is not null as paid "
        "from dues d join garages g on g.id=d.garage_id where g.member_id=? "
        "order by cast(g.number as integer), d.period", (member_id,))]


def list_all(conn):
    return conn.execute(
        "select d.*, g.number as garage, m.name from dues d join garages g on g.id=d.garage_id "
        "left join members m on m.id=g.member_id order by d.paid_at is not null, cast(g.number as integer), d.period"
    ).fetchall()


def mark_paid(conn, due_id):
    conn.execute("update dues set paid_at=coalesce(paid_at, datetime('now')) where id=?", (due_id,))


def summary(conn):
    r = conn.execute("select coalesce(sum(case when paid_at is null then amount end),0) datorat, "
                     "coalesce(sum(case when paid_at is not null then amount end),0) incasat from dues").fetchone()
    return dict(r)


def qr_png(garage_number, amount) -> bytes:
    text = f"DEMO - fara plata reala. CCG 18, garaj {garage_number}, suma {amount:.2f} MDL"
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
