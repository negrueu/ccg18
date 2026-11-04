import os
import pathlib
from dataclasses import dataclass


def _load_dotenv(path=".env"):
    """Citește .env ca TEXT (nu-l execută). Nu suprascrie variabilele deja setate."""
    p = pathlib.Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)


@dataclass
class Settings:
    bot_token: str
    secret_key: str
    base_url: str
    db_path: str
    seed_demo: bool
    admin_phones: list
    run_bot: bool
    upload_dir: str
    pay_beneficiary: str = ""
    pay_iban: str = ""
    pay_fiscal: str = ""
    pay_bank: str = ""
    tg_client_id: str = ""
    tg_client_secret: str = ""


def load() -> Settings:
    _load_dotenv()
    e = os.environ
    return Settings(
        bot_token=e.get("BOT_TOKEN", ""),
        secret_key=e.get("SECRET_KEY", "dev-secret"),
        base_url=e.get("BASE_URL", "http://localhost:8000").rstrip("/"),
        db_path=e.get("DB_PATH", "data/ccg18.db"),
        seed_demo=e.get("SEED_DEMO", "0") == "1",
        admin_phones=[p for p in e.get("ADMIN_PHONES", "").split(",") if p.strip()],
        run_bot=e.get("RUN_BOT", "1") == "1",
        upload_dir=e.get("UPLOAD_DIR", "data/uploads"),
        pay_beneficiary=e.get("PAY_BENEFICIARY", ""),
        pay_iban=e.get("PAY_IBAN", ""),
        pay_fiscal=e.get("PAY_FISCAL", ""),
        pay_bank=e.get("PAY_BANK", ""),
        tg_client_id=e.get("TG_CLIENT_ID", ""),
        tg_client_secret=e.get("TG_CLIENT_SECRET", ""),
    )
