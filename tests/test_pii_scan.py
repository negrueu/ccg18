import pathlib
import importlib.util

spec = importlib.util.spec_from_file_location(
    "pii_scan", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "pii_scan.py")
pii = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pii)


def _scan_text(tmp_path, text, name="x.py"):
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return pii.scan([str(f)])


def test_detects_real_phone_formats(tmp_path):
    # numerele sunt asamblate la rulare, ca fișierul de test să nu poarte el însuși un număr real
    real = "06" + "9" + "123456"
    for v in (real, "+373" + real[1:], "+373 " + real[1:3] + " " + real[3:6] + " " + real[6:]):
        assert _scan_text(tmp_path, f"tel = '{v}'"), v


def test_fictive_numbers_pass(tmp_path):
    assert _scan_text(tmp_path, "a = '000123456'; b = '+37300123456'; c = '37300111222'") == []


def test_email_iban_and_data_files(tmp_path):
    assert _scan_text(tmp_path, "x = 'ion" + "@" + "mail.md'")
    assert _scan_text(tmp_path, "iban = 'MD" + "56VI0223820000000" + "05MDL'")
    assert _scan_text(tmp_path, "nume,telefon", name="lista.csv")
