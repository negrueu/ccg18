import json
import sqlite3
from datetime import datetime, timezone


class VoteError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _now():
    return datetime.now(timezone.utc)


def create(conn, title, description, options, weight_mode, secret, deadline: datetime) -> int:
    if weight_mode not in ("member", "garage"):
        raise ValueError("weight_mode")
    opts = [o.strip() for o in options if o.strip()]
    if len(opts) < 2:
        raise ValueError("min 2 options")
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return conn.execute(
        "insert into votes(title, description, options_json, weight_mode, secret, deadline) values(?,?,?,?,?,?)",
        (title, description, json.dumps(opts, ensure_ascii=False), weight_mode, 1 if secret else 0,
         deadline.isoformat()),
    ).lastrowid


def get(conn, vote_id):
    return conn.execute("select * from votes where id=?", (vote_id,)).fetchone()


def options(v):
    return json.loads(v["options_json"])


def is_open(v, now=None):
    return v["status"] == "open" and datetime.fromisoformat(v["deadline"]) >= (now or _now())


def weight_for(conn, v, member_id) -> int:
    if v["weight_mode"] == "member":
        return 1
    n = conn.execute("select count(*) from garages where member_id=?", (member_id,)).fetchone()[0]
    return max(n, 1)


def cast(conn, vote_id, member_id, option_idx, entered_by=None, now=None) -> int:
    v = get(conn, vote_id)
    if not v:
        raise VoteError("not_found")
    if v["status"] != "open":
        raise VoteError("closed")
    if datetime.fromisoformat(v["deadline"]) < (now or _now()):
        raise VoteError("expired")
    if not (0 <= option_idx < len(options(v))):
        raise VoteError("bad_option")
    m = conn.execute("select status from members where id=?", (member_id,)).fetchone()
    if not m or m["status"] != "confirmat":
        raise VoteError("not_confirmed")
    w = weight_for(conn, v, member_id)
    conn.execute("begin immediate")
    try:
        conn.execute("insert into vote_cast(vote_id, member_id, entered_by) values(?,?,?)",
                     (vote_id, member_id, entered_by))
        conn.execute("insert into vote_choice(vote_id, option_idx, weight, member_id) values(?,?,?,?)",
                     (vote_id, option_idx, w, None if v["secret"] else member_id))
        conn.execute("commit")
    except sqlite3.IntegrityError:
        conn.execute("rollback")
        raise VoteError("already")
    except Exception:
        conn.execute("rollback")
        raise
    return w


def has_voted(conn, vote_id, member_id) -> bool:
    return conn.execute("select 1 from vote_cast where vote_id=? and member_id=?",
                        (vote_id, member_id)).fetchone() is not None


def close(conn, vote_id):
    conn.execute("update votes set status='closed' where id=?", (vote_id,))


def list_all(conn):
    return conn.execute("select * from votes order by id desc").fetchall()


def open_for(conn, member_id, now=None):
    st = conn.execute("select status from members where id=?", (member_id,)).fetchone()
    if not st or st["status"] != "confirmat":
        return []
    rows = conn.execute(
        "select * from votes where status='open' and id not in "
        "(select vote_id from vote_cast where member_id=?) order by id", (member_id,)).fetchall()
    return [r for r in rows if is_open(r, now)]


def tally(conn, vote_id) -> dict:
    v = get(conn, vote_id)
    opts = options(v)
    agg = {r["option_idx"]: r for r in conn.execute(
        "select option_idx, count(*) c, sum(weight) w from vote_choice where vote_id=? group by option_idx",
        (vote_id,))}
    total_members = conn.execute("select count(*) from members where status='confirmat'").fetchone()[0]
    if v["weight_mode"] == "garage":
        total_w = conn.execute(
            "select coalesce(sum(n),0) from (select max(count(g.id),1) n from members m "
            "left join garages g on g.member_id=m.id where m.status='confirmat' group by m.id)").fetchone()[0]
    else:
        total_w = total_members
    return {
        "options": [{"label": o, "count": agg[i]["c"] if i in agg else 0,
                     "weight": agg[i]["w"] if i in agg else 0} for i, o in enumerate(opts)],
        "voted_members": conn.execute("select count(*) from vote_cast where vote_id=?", (vote_id,)).fetchone()[0],
        "total_members": total_members,
        "total_weight_possible": total_w,
    }


def minutes(conn, vote_id) -> dict:
    v = get(conn, vote_id)
    opts = options(v)
    voters = []
    for r in conn.execute(
        "select m.id, m.name, c.cast_at, a.name as entered_by, "
        "(select option_idx from vote_choice ch where ch.vote_id=c.vote_id and ch.member_id=m.id) as opt, "
        "(select group_concat(number, ', ') from (select number from garages where member_id=m.id "
        " order by cast(number as integer))) as garaje "
        "from vote_cast c join members m on m.id=c.member_id left join members a on a.id=c.entered_by "
        "where c.vote_id=? order by m.name", (vote_id,)):
        voters.append({
            "name": r["name"], "garaje": r["garaje"] or "", "cast_at": r["cast_at"],
            "entered_by": r["entered_by"],
            "choice": None if v["secret"] or r["opt"] is None else opts[r["opt"]],
        })
    return {"vote": v, "options": opts, "tally": tally(conn, vote_id), "voters": voters}
