def log(conn, actor_id, action, detail=""):
    """Jurnal de audit: orice acțiune a unui administrator, cu autor și oră (UTC)."""
    conn.execute("insert into audit(actor_id, action, detail) values(?,?,?)", (actor_id, action, str(detail)[:500]))


def recent(conn, limit=200):
    return conn.execute(
        "select a.*, m.name as actor from audit a left join members m on m.id=a.actor_id "
        "order by a.id desc limit ?", (limit,)).fetchall()
