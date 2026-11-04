from . import members

MAX_PER_DAY = 5  # limită anti-spam per membru


def create(conn, member_id, text, photo_file_id=None, voice_file_id=None) -> int:
    return conn.execute("insert into reports(member_id, text, photo_file_id, voice_file_id) values(?,?,?,?)",
                        (member_id, text or "", photo_file_id, voice_file_id)).lastrowid


def allowed(conn, member_id) -> bool:
    n = conn.execute("select count(*) from reports where member_id=? and created_at > datetime('now','-1 day')",
                     (member_id,)).fetchone()[0]
    return n < MAX_PER_DAY


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
