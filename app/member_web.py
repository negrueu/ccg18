"""Site-ul pentru membri: aceleași funcții ca botul, cu intrare prin Telegram (Login Widget)."""
import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import audit, dues, members, notices, reports, votes
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
        "auth_url": st(request).settings.base_url + "/cont/tg"})


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
