import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import members, tg_oidc
from app.member_web import login_from_claims

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CID = "9185983665"


def _token(**over):
    now = int(time.time())
    claims = {"iss": tg_oidc.ISSUER, "aud": CID, "sub": "77", "id": 77, "iat": now, "exp": now + 3600,
              "nonce": "n1", "phone_number": "+37300000001", "phone_number_verified": True}
    claims.update(over)
    return jwt.encode(claims, KEY, algorithm="RS256")


def test_valid_token():
    c = tg_oidc.verify_id_token(_token(), CID, "n1", signing_key=KEY.public_key())
    assert tg_oidc.telegram_id(c) == 77


@pytest.mark.parametrize("over,nonce", [({"aud": "123"}, "n1"), ({"iss": "https://evil"}, "n1"),
                                        ({"exp": int(time.time()) - 100}, "n1"), ({}, "alt-nonce")])
def test_invalid_tokens(over, nonce):
    with pytest.raises(Exception):
        tg_oidc.verify_id_token(_token(**over), CID, nonce, signing_key=KEY.public_key())


def test_forged_signature_rejected():
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(Exception):
        tg_oidc.verify_id_token(_token(), CID, "n1", signing_key=other.public_key())


def test_login_by_verified_phone_links_account(conn):
    mid = members.upsert(conn, "Ion", "000000001", ["1"])
    claims = tg_oidc.verify_id_token(_token(), CID, "n1", signing_key=KEY.public_key())
    m, why = login_from_claims(conn, claims)
    assert why == "ok" and m["id"] == mid and m["telegram_id"] == 77
    assert login_from_claims(conn, {"id": 88, "sub": "88", "phone_number": "+37300999999"})[1] == "notfound"
    assert login_from_claims(conn, {"id": 99, "sub": "99"})[1] == "nophone"
    # același număr, alt cont Telegram -> blocat până aprobă conducerea
    assert login_from_claims(conn, {"id": 55, "sub": "55", "phone_number": "+37300000001"})[1] == "relink"


def test_pkce_flow_shape():
    f = tg_oidc.new_flow()
    url = tg_oidc.auth_redirect(CID, "https://ccg18.cc/cont/oidc/callback", f)
    assert "code_challenge_method=S256" in url and "scope=openid+profile+phone" in url and f["state"] in url
