"""Blochează date cu caracter personal în depozit (rulat în pre-commit și în CI).

Interzis în fișierele urmărite de git:
- numere de telefon reale din Moldova (373 6x/7x..., 06x/07x...) și alte numere internaționale lungi;
- adrese de e-mail (în afara listei albe);
- IBAN-uri;
- fișiere de date: .csv, .db, .sqlite, .xlsx, .xls, .vcf, .json din data/.
Numerele de test folosesc prefixul fictiv 3730 / 0000, care nu există în Moldova.
"""
import pathlib
import re
import subprocess
import sys

PATTERNS = {
    "telefon MD": re.compile(r"(?<!\d)(?:\+?373|0)\s?[67]\d(?:[\s-]?\d){6}(?!\d)"),
    "telefon international": re.compile(r"\+(?!373\s?0)\d[\d\s-]{9,14}\d"),
    "e-mail": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{6,}[A-Z0-9]*\b"),
}
EMAIL_ALLOW = {"noreply@anthropic.com"}
BLOCKED_EXT = {".csv", ".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".vcf"}
SKIP = {"scripts/pii_scan.py"}


def tracked(staged: bool):
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged else ["git", "ls-files"]
    return [p for p in subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines() if p]


def scan(paths):
    problems = []
    for p in paths:
        if p in SKIP:
            continue
        path = pathlib.Path(p)
        if path.suffix.lower() in BLOCKED_EXT or p.startswith("data/"):
            problems.append(f"{p}: fișier de date - nu se publică")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for label, rx in PATTERNS.items():
                for m in rx.finditer(line):
                    if label == "e-mail" and m.group(0) in EMAIL_ALLOW:
                        continue
                    problems.append(f"{p}:{n}: {label}: {m.group(0)}")
    return problems


if __name__ == "__main__":
    probs = scan(tracked("--staged" in sys.argv))
    if probs:
        print("DATE CU CARACTER PERSONAL GĂSITE - commit/CI blocat:")
        print("\n".join(probs))
        sys.exit(1)
    print("pii_scan: curat")
