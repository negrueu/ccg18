"""Telegram Login prin OpenID Connect (Authorization Code + PKCE), conform core.telegram.org/bots/telegram-login.

Fluxul: /cont/oidc/start -> oauth.telegram.org/auth -> /cont/oidc/callback?code&state
-> schimb codul pe token (Basic client_id:client_secret) -> validăm id_token (JWT, JWKS Telegram):
semnătură, iss, aud = client_id, exp, nonce. Scope `phone` aduce numărul verificat.
"""
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt

ISSUER = "https://oauth.telegram.org"
AUTH_URL = ISSUER + "/auth"
TOKEN_URL = ISSUER + "/token"
JWKS_URL = ISSUER + "/.well-known/jwks.json"

_jwks_cache = {"at": 0.0, "client": None}


def new_flow() -> dict:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return {"state": secrets.token_urlsafe(24), "nonce": secrets.token_urlsafe(24),
            "verifier": verifier, "challenge": challenge}


def auth_redirect(client_id: str, redirect_uri: str, flow: dict) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": "openid profile phone", "state": flow["state"], "nonce": flow["nonce"],
        "code_challenge": flow["challenge"], "code_challenge_method": "S256",
    })


async def exchange_code(client_id, client_secret, code, redirect_uri, verifier) -> str:
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(TOKEN_URL, headers={"Authorization": "Basic " + basic}, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": client_id, "code_verifier": verifier})
    r.raise_for_status()
    return r.json()["id_token"]


def _jwks_client():
    if _jwks_cache["client"] is None or time.time() - _jwks_cache["at"] > 3600:
        _jwks_cache["client"] = jwt.PyJWKClient(JWKS_URL)
        _jwks_cache["at"] = time.time()
    return _jwks_cache["client"]


def verify_id_token(id_token: str, client_id: str, nonce: str, signing_key=None) -> dict:
    """Ridică excepție dacă tokenul nu e valid. `signing_key` doar în teste."""
    key = signing_key if signing_key is not None else _jwks_client().get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(id_token, key, algorithms=["RS256", "ES256"], audience=str(client_id), issuer=ISSUER,
                        options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=30)
    if claims.get("nonce") != nonce:
        raise jwt.InvalidTokenError("nonce")
    return claims


def telegram_id(claims: dict):
    for k in ("id", "sub"):
        try:
            return int(claims[k])
        except (KeyError, ValueError, TypeError):
            continue
    return None
