"""Site-ul pentru membri: aceleași funcții ca botul, cu intrare prin Telegram (Login Widget)."""
import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import audit, auth, dues, members, notices, reports, tg_oidc, votes
from .web import templates

router = APIRouter(prefix="/cont")
COOKIE = "ccg18_member"
SESSION_AGE = 30 * 24 * 3600
TG_MAX_AGE = 24 * 3600


def st(request):
    return request.app.state


def signer(request):
    return URLSafeTimedSerializer(st(request).settings.secret_key, salt="member")


def verify_telegram_login(params: dict, bot_token: str, now=None):
    """Verificarea oficială Telegram Login Widget: secret = SHA256(token), hash = HMAC-SHA256(data_check_string)."""
    data = {k: v for k, v in params.items() if k != "hash"}
    got = params.get("hash", "")
    if not got or not bot_token or "id" not in data:
        return None
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hashlib.sha256(bot_token.encode()).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        if (now or time.time()) - int(data.get("auth_date", "0")) > TG_MAX_AGE:
            return None
        return int(data["id"])
    except ValueError:
        return None


def current_member(request: Request):
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401)
    try:
        mid = signer(request).loads(raw, max_age=SESSION_AGE)
    except BadSignature:
        raise HTTPException(401)
    m = members.get(st(request).conn, mid)
    if not m:
        raise HTTPException(401)
    return m


def csrf(request) -> str:
    raw = request.cookies.get(COOKIE, "")
    return hmac.new(st(request).settings.secret_key.encode(), ("mcsrf:" + raw).encode(), hashlib.sha256).hexdigest()


async def member_post(request: Request):
    m = current_member(request)
    form = await request.form()
    base = st(request).settings.base_url
    origin = request.headers.get("origin") or ""
    same = origin.rstrip("/") == base if origin else (request.headers.get("referer") or "").startswith(base + "/")
    if not same or not hmac.compare_digest(str(form.get("csrf", "")), csrf(request)):
        raise HTTPException(403)
    return m


def page(request, name, m, **kw):
    return templates.TemplateResponse(request, name, {"m": m, "csrf": csrf(request) if m else "", **kw})


@router.get("/intrare", response_class=HTMLResponse)
def login_page(request: Request, err: str = ""):
    return templates.TemplateResponse(request, "cont_intrare.html", {
        "bot_username": st(request).bot_username or "ccg18bot", "err": err,
        "oidc": bool(st(request).settings.tg_client_id and st(request).settings.tg_client_secret),
        "auth_url": st(request).settings.base_url + "/cont/tg"})


@router.get("/app", response_class=HTMLResponse)
def miniapp(request: Request):
    return templates.TemplateResponse(request, "cont_miniapp.html", {})


@router.post("/api/webapp-login")
async def webapp_login(request: Request):
    from fastapi.responses import JSONResponse
    body = await request.json()
    user = auth.verify_webapp_init_data(body.get("initData", ""), st(request).settings.bot_token)
    if not user:
        return JSONResponse({"ok": False, "error": "invalid"}, 401)
    m = members.by_telegram(st(request).conn, user.get("id"))
    if not m:
        return JSONResponse({"ok": False, "error": "unlinked"}, 403)
    secure = st(request).settings.base_url.startswith("https://")
    resp = JSONResponse({"ok": True})
    # în Telegram Web Mini App-ul rulează într-un iframe: cookie-ul trebuie SameSite=None; Secure
    resp.set_cookie(COOKIE, signer(request).dumps(m["id"]), max_age=SESSION_AGE, httponly=True,
                    secure=secure, samesite="none" if secure else "lax")
    return resp


FLOW_COOKIE = "ccg18_oidc"


def _flow_signer(request):
    return URLSafeTimedSerializer(st(request).settings.secret_key, salt="oidc-flow")


@router.get("/oidc/start")
def oidc_start(request: Request):
    s = st(request).settings
    if not (s.tg_client_id and s.tg_client_secret):
        return RedirectResponse("/cont/intrare", 303)
    flow = tg_oidc.new_flow()
    resp = RedirectResponse(tg_oidc.auth_redirect(s.tg_client_id, s.base_url + "/cont/oidc/callback", flow), 303)
    resp.set_cookie(FLOW_COOKIE, _flow_signer(request).dumps(flow), max_age=600, httponly=True,
                    secure=s.base_url.startswith("https://"), samesite="lax", path="/cont/oidc")
    return resp


def _login_response(request, m):
    secure = st(request).settings.base_url.startswith("https://")
    resp = RedirectResponse("/cont", 303)
    resp.set_cookie(COOKIE, signer(request).dumps(m["id"]), max_age=SESSION_AGE, httponly=True,
                    secure=secure, samesite="lax")
    resp.delete_cookie(FLOW_COOKIE, path="/cont/oidc")
    return resp


def login_from_claims(conn, claims):
    """Membrul din claims OIDC: după contul Telegram deja legat, altfel după telefonul VERIFICAT de Telegram.
    Returnează (member|None, motiv)."""
    tg_id = tg_oidc.telegram_id(claims)
    m = members.by_telegram(conn, tg_id) if tg_id else None
    if m:
        return m, "ok"
    phone = claims.get("phone_number")
    if not phone or claims.get("phone_number_verified") is False:
        return None, "nophone"
    m = members.find_by_phone(conn, str(phone))
    if not m:
        return None, "notfound"
    if m["telegram_id"] and m["telegram_id"] != tg_id:
        auth.kv_put_pending(conn, m["id"], tg_id)
        conn.execute("insert into audit(actor_id, action, detail) values(null, 'relegare_blocata', ?)",
                     (f"membru #{m['id']}: cont Telegram nou {tg_id} (site)",))
        return None, "relink"
    members.link_telegram(conn, m["id"], tg_id)
    return members.get(conn, m["id"]), "ok"


@router.get("/oidc/callback")
async def oidc_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    s = st(request).settings
    try:
        flow = _flow_signer(request).loads(request.cookies.get(FLOW_COOKIE, ""), max_age=600)
    except BadSignature:
        return RedirectResponse("/cont/intrare?err=invalid", 303)
    if error or not code or not hmac.compare_digest(state, flow["state"]):
        return RedirectResponse("/cont/intrare?err=invalid", 303)
    try:
        id_token = await tg_oidc.exchange_code(s.tg_client_id, s.tg_client_secret, code,
                                               s.base_url + "/cont/oidc/callback", flow["verifier"])
        claims = tg_oidc.verify_id_token(id_token, s.tg_client_id, flow["nonce"])
    except Exception:
        return RedirectResponse("/cont/intrare?err=invalid", 303)
    m, why = login_from_claims(st(request).conn, claims)
    if not m:
        return RedirectResponse(f"/cont/intrare?err={why}", 303)
    return _login_response(request, m)


@router.get("/tg")
def tg_callback(request: Request):
    tg_id = verify_telegram_login(dict(request.query_params), st(request).settings.bot_token)
    if not tg_id:
        return RedirectResponse("/cont/intrare?err=invalid", 303)
    m = members.by_telegram(st(request).conn, tg_id)
    if not m:
        # contul Telegram nu e legat de niciun membru: identificarea prin număr se face o dată, în bot
        return RedirectResponse("/cont/intrare?err=unlinked", 303)
    secure = st(request).settings.base_url.startswith("https://")
    resp = RedirectResponse("/cont", 303)
    resp.set_cookie(COOKIE, signer(request).dumps(m["id"]), max_age=SESSION_AGE, httponly=True,
                    secure=secure, samesite="lax")
    return resp


@router.post("/iesire")
def logout(m=Depends(member_post)):
    r = RedirectResponse("/cont/intrare", 303)
    r.delete_cookie(COOKIE)
    return r


@router.get("", response_class=HTMLResponse)
def home(request: Request, m=Depends(current_member)):
    c = st(request).conn
    s = st(request).settings
    my_dues = dues.for_member(c, m["id"])
    unpaid = [d for d in my_dues if not d["paid"]]
    garages = members.garages(c, m["id"])
    gstr = ", ".join(g["number"] for g in garages) or "-"
    my_votes = c.execute(
        "select v.id, v.title, v.secret, v.status, vc.cast_at, vc.entered_by, "
        "(select option_idx from vote_choice ch where ch.vote_id=v.id and ch.member_id=?) as opt, v.options_json "
        "from vote_cast vc join votes v on v.id=vc.vote_id where vc.member_id=? order by v.id desc",
        (m["id"], m["id"])).fetchall()
    import json
    votes_hist = [{"title": r["title"], "secret": r["secret"], "when": r["cast_at"],
                   "paper": bool(r["entered_by"]),
                   "choice": None if r["secret"] or r["opt"] is None else json.loads(r["options_json"])[r["opt"]]}
                  for r in my_votes]
    my_reports = c.execute("select id, text, status, created_at from reports where member_id=? order by id desc limit 20",
                           (m["id"],)).fetchall()
    pay = None
    if s.pay_iban and unpaid:
        period = max(d["period"] for d in unpaid)
        pay = {"beneficiary": s.pay_beneficiary, "fiscal": s.pay_fiscal, "iban": s.pay_iban, "bank": s.pay_bank,
               "amount": sum(d["amount"] for d in unpaid),
               "purpose": dues.payment_purpose(period, m["name"], gstr)}
    return page(request, "cont.html", m, garages=garages, open_votes=votes.open_for(c, m["id"]),
                dues=my_dues, total_due=sum(d["amount"] for d in unpaid), pay=pay,
                news=notices.recent_for(c, m["id"], 10), votes_hist=votes_hist, my_reports=my_reports,
                open_reports=sum(1 for r in my_reports if r["status"] != "rezolvat"),
                confirmed=members.is_confirmed(m))


@router.get("/vot/{vid}", response_class=HTMLResponse)
def vote_page(vid: int, request: Request, m=Depends(current_member), err: str = ""):
    c = st(request).conn
    v = votes.get(c, vid)
    if not v:
        raise HTTPException(404)
    return page(request, "cont_vot.html", m, v=v, opts=votes.options(v), voted=votes.has_voted(c, vid, m["id"]),
                weight=votes.weight_for(c, v, m["id"]), is_open=votes.is_open(v), err=err)


@router.post("/vot/{vid}")
def vote_cast(vid: int, request: Request, option_idx: int = Form(...), confirm: str = Form(""),
              m=Depends(member_post)):
    if confirm != "da":
        return RedirectResponse(f"/cont/vot/{vid}?err=confirm", 303)
    try:
        votes.cast(st(request).conn, vid, m["id"], option_idx)
    except votes.VoteError as e:
        return RedirectResponse(f"/cont/vot/{vid}?err={e.code}", 303)
    return RedirectResponse(f"/cont/vot/{vid}", 303)


@router.post("/sesizare")
def report(request: Request, text: str = Form(...), m=Depends(member_post)):
    c = st(request).conn
    text = text.strip()[:2000]
    if not text:
        return RedirectResponse("/cont?msg=gol", 303)
    if not reports.allowed(c, m["id"]):
        return RedirectResponse("/cont?msg=limita", 303)
    rid = reports.create(c, m["id"], text)
    audit.log(c, None, "sesizare_site", f"#{rid} de la membru #{m['id']}")
    return RedirectResponse(f"/cont?msg=sesizare{rid}", 303)
