from . import members


def create(conn, member_id, text, photo_file_id=None) -> int:
    return conn.execute("insert into reports(member_id, text, photo_file_id) values(?,?,?)",
                        (member_id, text or "", photo_file_id)).lastrowid


def list_all(conn):
    return conn.execute(
        "select r.*, m.name, (select group_concat(number, ', ') from garages where member_id=m.id) garaje "
        "from reports r join members m on m.id=r.member_id order by r.status='rezolvat', r.id desc").fetchall()


def get(conn, rid):
    return conn.execute("select * from reports where id=?", (rid,)).fetchone()


def resolve(conn, rid):
    conn.execute("update reports set status='rezolvat' where id=?", (rid,))
    r = get(conn, rid)
    return members.get(conn, r["member_id"]) if r else None
