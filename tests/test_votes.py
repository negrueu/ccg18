from datetime import datetime, timedelta, timezone
import pytest
from app import votes, members
from app.votes import VoteError

FUT = datetime.now(timezone.utc) + timedelta(days=3)


def _people(conn):
    a = members.upsert(conn, "Trei Garaje", "000000001", ["1", "2", "3"])
    b = members.upsert(conn, "Un Garaj", "000000002", ["4"])
    adm = members.upsert(conn, "Admin", "000000003", ["5"], is_admin=True)
    return a, b, adm


def test_weight_by_garage_and_member(conn):
    a, b, _ = _people(conn)
    vg = votes.create(conn, "Buget", "", ["Pentru", "Împotrivă"], "garage", False, FUT)
    vm = votes.create(conn, "Buget2", "", ["Pentru", "Împotrivă"], "member", False, FUT)
    assert votes.cast(conn, vg, a, 0) == 3
    assert votes.cast(conn, vm, a, 0) == 1
    t = votes.tally(conn, vg)
    assert t["options"][0]["weight"] == 3 and t["options"][0]["count"] == 1
    assert t["total_weight_possible"] == 5


def test_double_vote_rejected(conn):
    a, _, _ = _people(conn)
    v = votes.create(conn, "X", "", ["Da", "Nu"], "member", False, FUT)
    votes.cast(conn, v, a, 0)
    with pytest.raises(VoteError) as e:
        votes.cast(conn, v, a, 1)
    assert e.value.code == "already"
    t = votes.tally(conn, v)
    assert [o["count"] for o in t["options"]] == [1, 0]


def test_closed_and_expired(conn):
    a, b, _ = _people(conn)
    v = votes.create(conn, "X", "", ["Da", "Nu"], "member", False, FUT)
    votes.close(conn, v)
    with pytest.raises(VoteError) as e:
        votes.cast(conn, v, a, 0)
    assert e.value.code == "closed"
    v2 = votes.create(conn, "Y", "", ["Da", "Nu"], "member", False, FUT)
    with pytest.raises(VoteError) as e:
        votes.cast(conn, v2, b, 0, now=FUT + timedelta(seconds=1))
    assert e.value.code == "expired"
    with pytest.raises(VoteError) as e:
        votes.cast(conn, v2, b, 7)
    assert e.value.code == "bad_option"


def test_secret_vote_unlinkable(conn):
    a, b, _ = _people(conn)
    v = votes.create(conn, "Președinte", "", ["A", "B"], "member", True, FUT)
    votes.cast(conn, v, a, 0)
    votes.cast(conn, v, b, 1)
    assert conn.execute("select count(*) from vote_choice where vote_id=? and member_id is not null",
                        (v,)).fetchone()[0] == 0
    m = votes.minutes(conn, v)
    assert all(p["choice"] is None for p in m["voters"])


def test_paper_vote_in_minutes(conn):
    a, _, adm = _people(conn)
    v = votes.create(conn, "X", "", ["Da", "Nu"], "member", False, FUT)
    votes.cast(conn, v, a, 1, entered_by=adm)
    p = votes.minutes(conn, v)["voters"][0]
    assert p["entered_by"] == "Admin" and p["choice"] == "Nu"
    assert "1, 2, 3" == p["garaje"]


def test_unconfirmed_cannot_vote_and_not_in_quorum(conn):
    a, b, _ = _people(conn)
    members.import_viber_csv(conn, "nume_viber,telefon\nVecin,+37300555666\n")
    u = members.find_by_phone(conn, "37300555666")
    assert u["status"] == "neconfirmat"
    v = votes.create(conn, "X", "", ["Da", "Nu"], "member", False, FUT)
    with pytest.raises(VoteError) as e:
        votes.cast(conn, v, u["id"], 0)
    assert e.value.code == "not_confirmed"
    assert votes.open_for(conn, u["id"]) == []
    assert votes.tally(conn, v)["total_members"] == 3
    members.confirm(conn, u["id"], "Ion Vecinu", ["77"], "C")
    assert votes.cast(conn, v, u["id"], 1) == 1
    assert votes.tally(conn, v)["total_members"] == 4


def test_viber_import_does_not_override_registry(conn):
    members.upsert(conn, "Nume Oficial", "000000009", ["9"])
    n = members.import_viber_csv(conn, "nume_viber,telefon\nPorecla,+37300000009\nAltul,+37300000010\n")
    assert n == 1
    assert members.find_by_phone(conn, "000000009")["name"] == "Nume Oficial"


def test_open_for(conn):
    a, b, _ = _people(conn)
    v = votes.create(conn, "X", "", ["Da", "Nu"], "member", False, FUT)
    votes.cast(conn, v, a, 0)
    assert [r["id"] for r in votes.open_for(conn, a)] == []
    assert [r["id"] for r in votes.open_for(conn, b)] == [v]


def test_secret_vote_order_does_not_link(conn):
    ids = [members.upsert(conn, f"M{i}", f"0000001{i:02d}", [str(100 + i)]) for i in range(12)]
    v = votes.create(conn, "S", "", ["A", "B"], "member", True, FUT)
    for i, mid in enumerate(ids):
        votes.cast(conn, v, mid, i % 2)
    casts = [r["member_id"] for r in conn.execute("select member_id from vote_cast where vote_id=? order by rowid", (v,))]
    choices = [r["option_idx"] for r in conn.execute("select option_idx from vote_choice where vote_id=? order by id", (v,))]
    assert choices != [i % 2 for i in range(12)]  # ordinea pe id nu mai reproduce ordinea votării
    assert all(len(r["cast_at"]) == 10 for r in conn.execute("select cast_at from vote_cast where vote_id=?", (v,)))


def test_garage_cannot_be_stolen(conn):
    a = members.upsert(conn, "Proprietar", "000000050", ["1", "2", "3"])
    import pytest
    with pytest.raises(members.GarageConflict):
        members.upsert(conn, "Intrus", "000000051", ["1", "2", "3", "4"])
    assert [g["number"] for g in members.garages(conn, a)] == ["1", "2", "3"]
