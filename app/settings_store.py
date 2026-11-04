def get(conn, key, default=None):
    conn.execute("create table if not exists kv(key text primary key, value text)")
    r = conn.execute("select value from kv where key=?", (key,)).fetchone()
    return r[0] if r else default


def put(conn, key, value):
    conn.execute("create table if not exists kv(key text primary key, value text)")
    conn.execute("insert into kv(key, value) values(?,?) on conflict(key) do update set value=excluded.value",
                 (key, None if value is None else str(value)))
