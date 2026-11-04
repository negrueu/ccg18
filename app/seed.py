import random
from . import members

FIRST = ["Ion", "Vasile", "Gheorghe", "Nicolae", "Andrei", "Sergiu", "Mihai", "Valeriu", "Igor", "Dumitru",
         "Elena", "Maria", "Ana", "Tatiana", "Natalia"]
LAST = ["Popa", "Rusu", "Ciobanu", "Lungu", "Munteanu", "Cojocaru", "Țurcanu", "Rotari", "Ceban", "Moraru",
        "Sârbu", "Bivol", "Guțu", "Melnic", "Botnaru"]

# 30 de membri demo, 40 de garaje: 2 membri cu 3 garaje, 6 cu 2, restul cu 1.
GARAGE_COUNTS = [3, 3] + [2] * 6 + [1] * 22


def run(conn, admin_phones):
    demo_exists = conn.execute("select count(*) from members where phone like '37300000%'").fetchone()[0]
    if not demo_exists:
        rnd = random.Random(18)
        nr = 1
        for i, n in enumerate(GARAGE_COUNTS):
            gs = list(range(nr, nr + n))
            nr += n
            row = "ABCD"[(gs[0] - 1) // 10]
            members.upsert(conn, f"{rnd.choice(FIRST)} {rnd.choice(LAST)}", f"0000{i:05d}", gs, row)
        for g in conn.execute("select id, number from garages").fetchall():
            for period, amount in (("2025", 600), ("2026", 650)):
                paid = "2025-03-01" if period == "2025" and int(g["number"]) % 3 else None
                conn.execute("insert or ignore into dues(garage_id, period, amount, paid_at) values(?,?,?,?)",
                             (g["id"], period, amount, paid))
    run_admins(conn, admin_phones)


def run_admins(conn, admin_phones):
    for i, p in enumerate(admin_phones):
        if not p.strip():
            continue
        existing = members.find_by_phone(conn, p)
        if existing:
            conn.execute("update members set is_admin=1 where id=?", (existing["id"],))
            continue
        mid = members.upsert(conn, f"Administrator {i + 1}", p, [], None, is_admin=True)
        conn.execute("update members set status='confirmat', source='panou' where id=?", (mid,))
