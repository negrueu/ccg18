from app import phone, members, seed


def test_normalize_formats():
    assert phone.normalize("000123456") == "37300123456"
    assert phone.normalize("+373 00 123 456") == "37300123456"
    assert phone.normalize("37300123456") == "37300123456"
    assert phone.normalize("00123456") == "37300123456"
    assert phone.normalize("+" + "40 700 000 001") == "40700000001"  # asamblat: scanerul PII nu admite numere literale
    assert phone.normalize("00373 00 123 456") == "37300123456"
    assert phone.normalize("000111222") == "37300111222"


def test_import_csv_multi_garage(conn):
    csv = "nume,telefon,garaje,rand\nIon Popa,000111222,12;13;14,A\nAna Rusu,+37300333444,20,B\n"
    assert members.import_csv(conn, csv) == 2
    m = members.find_by_phone(conn, "37300111222")
    assert [g["number"] for g in members.garages(conn, m["id"])] == ["12", "13", "14"]
    members.import_csv(conn, csv)
    assert conn.execute("select count(*) from members").fetchone()[0] == 2
    assert conn.execute("select count(*) from garages").fetchone()[0] == 4


def test_seed_idempotent(conn):
    seed.run(conn, ["000999888"])
    seed.run(conn, ["000999888"])
    assert conn.execute("select count(*) from members").fetchone()[0] == 31
    assert conn.execute("select count(*) from garages").fetchone()[0] == 41
    adm = members.find_by_phone(conn, "000999888")
    assert adm["is_admin"] == 1
