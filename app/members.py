import csv
import io
from .phone import normalize


def find_by_phone(conn, raw):
    return conn.execute("select * from members where phone=?", (normalize(raw),)).fetchone()


def by_telegram(conn, tg_id):
    return conn.execute("select * from members where telegram_id=?", (tg_id,)).fetchone()


def get(conn, member_id):
    return conn.execute("select * from members where id=?", (member_id,)).fetchone()


def link_telegram(conn, member_id, telegram_id):
    conn.execute("update members set telegram_id=null where telegram_id=? and id<>?", (telegram_id, member_id))
    conn.execute("update members set telegram_id=? where id=?", (telegram_id, member_id))


def set_lang(conn, member_id, lang):
    conn.execute("update members set lang=? where id=?", (lang, member_id))


def garages(conn, member_id):
    return conn.execute(
        "select * from garages where member_id=? order by cast(number as integer), number", (member_id,)
    ).fetchall()


class GarageConflict(Exception):
    def __init__(self, garages):
        super().__init__(", ".join(garages))
        self.garages = garages


def upsert(conn, name, raw_phone, garage_numbers, row_label=None, is_admin=None):
    p = normalize(raw_phone)
    wanted = [str(g).strip() for g in garage_numbers if str(g).strip()]
    if wanted:
        taken = [r["number"] for r in conn.execute(
            "select g.number from garages g join members m on m.id=g.member_id where g.number in (%s) and m.phone<>?"
            % ",".join("?" * len(wanted)), (*wanted, p))]
        if taken:
            raise GarageConflict(taken)
    row = conn.execute("select id from members where phone=?", (p,)).fetchone()
    if row:
        mid = row["id"]
        conn.execute("update members set name=? where id=?", (name, mid))
    else:
        mid = conn.execute("insert into members(name, phone) values(?,?)", (name, p)).lastrowid
    if is_admin is not None:
        conn.execute("update members set is_admin=? where id=?", (1 if is_admin else 0, mid))
    for g in garage_numbers:
        g = str(g).strip()
        if not g:
            continue
        conn.execute(
            "insert into garages(number, row_label, member_id) values(?,?,?) "
            "on conflict(number) do update set member_id=excluded.member_id, "
            "row_label=coalesce(excluded.row_label, garages.row_label)",
            (g, row_label, mid),
        )
    return mid


def import_csv(conn, text: str) -> int:
    n = 0
    sample = text.lstrip("﻿")
    delim = ";" if sample.split("\n", 1)[0].count(";") > sample.split("\n", 1)[0].count(",") else ","
    for r in csv.DictReader(io.StringIO(sample), delimiter=delim):
        r = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        if not r.get("telefon") or not r.get("nume"):
            continue
        sep = "," if delim == ";" else ";"
        try:
            mid = upsert(conn, r["nume"], r["telefon"], r.get("garaje", "").replace(sep, " ").split(),
                         r.get("rand") or None)
        except GarageConflict:
            continue  # garajul aparține altui membru: rândul se sare, nu se reatribuie tacit
        conn.execute("update members set status='confirmat', source='registru' where id=?", (mid,))
        n += 1
    return n


def import_viber_csv(conn, text: str) -> int:
    """Lista de participanți din grupul Viber: membri NECONFIRMAȚI, fără garaje.
    Nu suprascrie un membru deja existent (registrul are prioritate)."""
    n = 0
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        tel = (r.get("telefon") or "").strip()
        if not tel or find_by_phone(conn, tel):
            continue
        name = (r.get("nume_viber") or "").strip() or "Membru nou"
        conn.execute("insert into members(name, phone, status, source, created_at) "
                     "values(?,?,'neconfirmat','viber',datetime('now'))", (name, normalize(tel)))
        n += 1
    return n


def set_self_info(conn, member_id, name=None, garages=None):
    if name is not None:
        conn.execute("update members set self_name=? where id=?", (name.strip()[:120], member_id))
    if garages is not None:
        conn.execute("update members set self_garages=? where id=?", (garages.strip()[:200], member_id))


def confirm(conn, member_id, name, garage_numbers, row_label=None):
    m = get(conn, member_id)
    upsert(conn, name.strip(), m["phone"], garage_numbers, row_label)
    conn.execute("update members set status='confirmat' where id=?", (member_id,))


def is_confirmed(m) -> bool:
    return m is not None and m["status"] == "confirmat"


def all_members(conn):
    return conn.execute(
        "select m.*, group_concat(g.number, ', ') as garaje, count(g.id) as nr_garaje "
        "from members m left join garages g on g.member_id=m.id group by m.id order by m.name"
    ).fetchall()


def rows(conn):
    return [r[0] for r in conn.execute(
        "select distinct row_label from garages where row_label is not null order by 1")]
