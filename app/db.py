import sqlite3
import pathlib

SCHEMA = """
create table if not exists members(
  id integer primary key, name text not null, phone text unique not null,
  telegram_id integer unique, lang text default 'ro', is_admin integer default 0);
create table if not exists garages(
  id integer primary key, number text unique not null, row_label text,
  member_id integer references members(id));
create table if not exists votes(
  id integer primary key, title text not null, description text default '',
  options_json text not null, weight_mode text not null default 'member',
  secret integer not null default 0, deadline text not null,
  status text not null default 'open', created_at text default (datetime('now')));
create table if not exists vote_cast(
  vote_id integer references votes(id), member_id integer references members(id),
  entered_by integer references members(id), cast_at text default (datetime('now')),
  primary key(vote_id, member_id));
create table if not exists vote_choice(
  id integer primary key, vote_id integer references votes(id), option_idx integer not null,
  weight integer not null, member_id integer references members(id));
create table if not exists notices(
  id integer primary key, kind text not null, title text not null, body text default '',
  row_label text, file_path text, created_at text default (datetime('now')));
create table if not exists notice_delivery(
  notice_id integer references notices(id), member_id integer references members(id),
  delivered_at text default (datetime('now')), ack_at text,
  primary key(notice_id, member_id));
create table if not exists dues(
  id integer primary key, garage_id integer references garages(id), period text not null,
  amount real not null, paid_at text, unique(garage_id, period));
create table if not exists reports(
  id integer primary key, member_id integer references members(id), text text default '',
  photo_file_id text, status text not null default 'nou', created_at text default (datetime('now')));
create table if not exists admin_tokens(
  token text primary key, member_id integer references members(id), expires_at text not null);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("pragma foreign_keys=on")
    if path != ":memory:":
        c.execute("pragma journal_mode=wal")
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("pragma table_info(members)")}
    for col, ddl in (("blocked_at", "text"),
                     ("status", "text not null default 'confirmat'"),  # confirmat | neconfirmat
                     ("source", "text default 'registru'"),            # registru | viber | panou
                     ("self_name", "text"), ("self_garages", "text"),
                     ("created_at", "text")):
        if col not in cols:
            c.execute(f"alter table members add column {col} {ddl}")
    if "voice_file_id" not in {r[1] for r in c.execute("pragma table_info(reports)")}:
        c.execute("alter table reports add column voice_file_id text")
    c.execute("""create table if not exists audit(
        id integer primary key, at text default (datetime('now')), actor_id integer, action text not null,
        detail text default '')""")
    return c
