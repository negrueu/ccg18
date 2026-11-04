import asyncio
import logging
import pathlib
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import audit, auth, dues, members, notices, reports, votes
from . import bot as botmod
from . import settings_store as kv
from .i18n import t

log = logging.getLogger("ccg18.web")
TZ = ZoneInfo("Europe/Chisinau")
COOKIE = "ccg18_admin"
SESSION_AGE = 12 * 3600
templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent / "templates"))
templates.env.filters["dt"] = botmod.fmt_dt
router = APIRouter()


def ctx(request: Request):
    return request.app.state


def signer(request):
    return URLSafeTimedSerializer(ctx(request).settings.secret_key, salt="admin")


def current_admin(request: Request):
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401)
    try:
        mid = signer(request).loads(raw, max_age=SESSION_AGE)
    except BadSignature:
        raise HTTPException(401)
    m = members.get(ctx(request).conn, mid)
    if not m or not m["is_admin"]:
        raise HTTPException(401)
    return m


def csrf_token(request) -> str:
    """Token legat de sesiune: HMAC(secret, cookie). Nu poate fi ghicit de un site străin."""
    import hashlib
    import hmac
    raw = request.cookies.get(COOKIE, "")
    return hmac.new(ctx(request).settings.secret_key.encode(), ("csrf:" + raw).encode(), hashlib.sha256).hexdigest()


def _same_origin(request) -> bool:
    base = ctx(request).settings.base_url
    origin = request.headers.get("origin") or ""
    if origin:
        return origin.rstrip("/") == base
    ref = request.headers.get("referer") or ""
    return ref.startswith(base + "/")


async def admin_post(request: Request):
    """Admin + protecție CSRF pentru orice POST: aceeași origine ȘI token valid în formular."""
    admin = current_admin(request)
    form = await request.form()
    import hmac
    if not _same_origin(request) or not hmac.compare_digest(str(form.get("csrf", "")), csrf_token(request)):
        raise HTTPException(403)
    return admin


def _session(request, resp, member_id):
    secure = ctx(request).settings.base_url.startswith("https://")
    resp.set_cookie(COOKIE, signer(request).dumps(member_id), max_age=SESSION_AGE, httponly=True,
                    secure=secure, samesite="none" if secure else "lax")
    return resp


def render(request, name, admin, **kw):
    return templates.TemplateResponse(request, name, {"admin": admin, "csrf": csrf_token(request), **kw})


def spawn(request, coro):
    task = asyncio.create_task(coro)
    ctx(request).tasks.add(task)
    task.add_done_callback(ctx(request).tasks.discard)


# ---------- autentificare ----------
@router.get("/login/{token}")
def login(token: str, request: Request):
    m = auth.consume_admin_token(ctx(request).conn, token)
    if not m:
        return HTMLResponse("<p>Link expirat sau deja folosit. Trimiteți /panou în bot.</p>", 401)
    return _session(request, RedirectResponse("/panou", 303), m["id"])


@router.get("/app", response_class=HTMLResponse)
def miniapp(request: Request):
    return templates.TemplateResponse(request, "miniapp.html", {})


@router.post("/api/webapp-login")
async def webapp_login(request: Request):
    body = await request.json()
    user = auth.verify_webapp_init_data(body.get("initData", ""), ctx(request).settings.bot_token)
    if not user:
        return JSONResponse({"ok": False, "error": "invalid"}, 401)
    m = members.by_telegram(ctx(request).conn, user.get("id"))
    if not m or not m["is_admin"]:
        return JSONResponse({"ok": False, "error": "not_admin"}, 403)
    return _session(request, JSONResponse({"ok": True}), m["id"])


@router.post("/logout")
def logout(admin=Depends(admin_post)):
    r = RedirectResponse("/login-info", 303)
    r.delete_cookie(COOKIE)
    return r


@router.get("/login-info", response_class=HTMLResponse)
def login_info():
    return HTMLResponse("<p>Intrați din bot: trimiteți /panou lui @ccg18bot.</p>")


# ---------- tablou ----------
@router.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {"bot_username": ctx(request).bot_username or "ccg18bot"})


@router.get("/qr-bot.png")
def qr_bot(request: Request):
    import io
    import qrcode
    img = qrcode.make(f"https://t.me/{ctx(request).bot_username or 'ccg18bot'}?start=qr", box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/panou", response_class=HTMLResponse)
def home(request: Request, admin=Depends(current_admin)):
    c = ctx(request).conn
    s = {
        "members": c.execute("select count(*) from members").fetchone()[0],
        "linked": c.execute("select count(*) from members where telegram_id is not null").fetchone()[0],
        "garages": c.execute("select count(*) from garages").fetchone()[0],
        "open_votes": c.execute("select count(*) from votes where status='open'").fetchone()[0],
        "new_reports": c.execute("select count(*) from reports where status='nou'").fetchone()[0],
        **dues.summary(c),
    }
    return render(request, "home.html", admin, s=s, votes=votes.list_all(c)[:5])


# ---------- membri ----------
@router.get("/membri", response_class=HTMLResponse)
def membri(request: Request, admin=Depends(current_admin), msg: str = "", f: str = ""):
    rows = members.all_members(ctx(request).conn)
    if f in ("confirmat", "neconfirmat"):
        rows = [r for r in rows if r["status"] == f]
    return render(request, "membri.html", admin, rows=rows, msg=msg, f=f)


@router.post("/membri/import")
async def membri_import(request: Request, file: UploadFile = File(...), admin=Depends(admin_post)):
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
    c = ctx(request).conn
    head = text.lstrip("﻿").split("\n", 1)[0].lower()
    if "nume_viber" in head:
        n = members.import_viber_csv(c, text)
        kind = "neconfirmati (lista Viber)"
    else:
        n = members.import_csv(c, text)
        kind = "confirmati (registru)"
    audit.log(c, admin["id"], "import_membri", f"{n} {kind}, fisier {file.filename}")
    return RedirectResponse(f"/membri?msg=Importati: {n} {kind}", 303)


@router.post("/membri/adauga")
def membri_adauga(request: Request, nume: str = Form(...), telefon: str = Form(...), garaje: str = Form(""),
                  rand: str = Form(""), is_admin: str = Form(""), admin=Depends(admin_post)):
    c = ctx(request).conn
    gs = garaje.replace(";", ",").replace(" ", ",").split(",")
    try:
        mid = members.upsert(c, nume.strip(), telefon, gs, rand.strip() or None, is_admin=True if is_admin else None)
    except members.GarageConflict as e:
        return RedirectResponse(f"/membri?msg=Garajele {e} aparțin altui membru. Eliberați-le întâi.", 303)
    c.execute("update members set status='confirmat', source=coalesce(source,'panou') where id=?", (mid,))
    audit.log(c, admin["id"], "membru_salvat", f"#{mid} {nume.strip()}, garaje {garaje}, admin={bool(is_admin)}")
    return RedirectResponse("/membri?msg=Membru salvat", 303)


@router.post("/membri/{mid}/confirma")
def membri_confirma(mid: int, request: Request, nume: str = Form(...), garaje: str = Form(...),
                    rand: str = Form(""), admin=Depends(admin_post)):
    st = ctx(request)
    gs = [x for x in garaje.replace(";", ",").replace(" ", ",").split(",") if x.strip()]
    if not gs:
        return RedirectResponse("/membri?f=neconfirmat&msg=Garajele sunt obligatorii la confirmare", 303)
    try:
        members.confirm(st.conn, mid, nume, gs, rand.strip() or None)
    except members.GarageConflict as e:
        return RedirectResponse(f"/membri?f=neconfirmat&msg=Garajele {e} aparțin altui membru - verificați registrul.", 303)
    audit.log(st.conn, admin["id"], "membru_confirmat", f"#{mid} {nume.strip()}, garaje {', '.join(gs)}")
    m = members.get(st.conn, mid)
    if m["telegram_id"] and st.bot:
        spawn(request, botmod.notify(st.bot, m["telegram_id"], t(m["lang"], "confirmed_msg", name=botmod.esc(m["name"]),
                                                                   garages=botmod.garages_str(st.conn, mid))))
    return RedirectResponse("/membri?f=neconfirmat&msg=Confirmat: " + nume.strip(), 303)


@router.get("/jurnal", response_class=HTMLResponse)
def jurnal(request: Request, admin=Depends(current_admin)):
    c = ctx(request).conn
    pending = c.execute("select m.id, m.name from kv join members m on kv.key = 'relink:' || m.id "
                        "where kv.value is not null").fetchall() if c.execute(
        "select 1 from sqlite_master where name='kv'").fetchone() else []
    return render(request, "jurnal.html", admin, items=audit.recent(c), pending=pending,
                  group={"title": kv.get(c, "group_title"), "invite": kv.get(c, "group_invite")})


@router.post("/membri/{mid}/relegare")
def membri_relegare(mid: int, request: Request, admin=Depends(admin_post)):
    st = ctx(request)
    new_tg = auth.approve_relink(st.conn, mid)
    if new_tg:
        audit.log(st.conn, admin["id"], "relegare_aprobata", f"membru #{mid} -> cont Telegram {new_tg}")
    return RedirectResponse("/jurnal", 303)


# ---------- voturi ----------
@router.get("/voturi", response_class=HTMLResponse)
def voturi(request: Request, admin=Depends(current_admin)):
    return render(request, "voturi.html", admin, items=votes.list_all(ctx(request).conn))


@router.post("/voturi")
def voturi_create(request: Request, title: str = Form(...), description: str = Form(""),
                  options: str = Form(...), weight_mode: str = Form("member"), secret: str = Form(""),
                  deadline: str = Form(...), admin=Depends(admin_post)):
    dl = datetime.fromisoformat(deadline).replace(tzinfo=TZ)
    vid = votes.create(ctx(request).conn, title.strip(), description.strip(), options.splitlines(),
                       weight_mode, bool(secret), dl)
    st = ctx(request)
    audit.log(st.conn, admin["id"], "vot_creat", f"#{vid} {title.strip()} ({weight_mode}, secret={bool(secret)}, termen {deadline})")
    if st.bot:
        spawn(request, botmod.broadcast_vote(st.bot, st.conn, vid))
    return RedirectResponse(f"/voturi/{vid}", 303)


@router.get("/voturi/{vid}", response_class=HTMLResponse)
def vot(vid: int, request: Request, admin=Depends(current_admin), err: str = ""):
    c = ctx(request).conn
    v = votes.get(c, vid)
    if not v:
        raise HTTPException(404)
    not_voted = c.execute("select m.* from members m where m.status='confirmat' and m.telegram_id is null "
                          "and m.id not in (select member_id from vote_cast where vote_id=?) order by m.name",
                          (vid,)).fetchall()
    return render(request, "vot.html", admin, v=v, tally=votes.tally(c, vid), opts=votes.options(v),
                  not_voted=not_voted, is_open=votes.is_open(v), err=err, visible=votes.results_visible(v))


@router.post("/voturi/{vid}/hartie")
def vot_hartie(vid: int, request: Request, member_id: int = Form(...), option_idx: int = Form(...),
               admin=Depends(admin_post)):
    m = members.get(ctx(request).conn, member_id)
    if not m or m["telegram_id"]:
        return RedirectResponse(f"/voturi/{vid}?err=has_telegram", 303)
    try:
        votes.cast(ctx(request).conn, vid, member_id, option_idx, entered_by=admin["id"])
    except votes.VoteError as e:
        return RedirectResponse(f"/voturi/{vid}?err={e.code}", 303)
    v = votes.get(ctx(request).conn, vid)
    det = f"vot #{vid}, membru #{member_id}" + ("" if v["secret"] else f", varianta {option_idx}")
    audit.log(ctx(request).conn, admin["id"], "vot_pe_hartie", det)
    return RedirectResponse(f"/voturi/{vid}", 303)


@router.post("/voturi/{vid}/inchide")
def vot_inchide(vid: int, request: Request, admin=Depends(admin_post)):
    votes.close(ctx(request).conn, vid)
    audit.log(ctx(request).conn, admin["id"], "vot_inchis", f"#{vid}")
    return RedirectResponse(f"/voturi/{vid}", 303)


@router.get("/voturi/{vid}/pv", response_class=HTMLResponse)
def vot_pv(vid: int, request: Request, admin=Depends(current_admin)):
    c = ctx(request).conn
    if not votes.get(c, vid):
        raise HTTPException(404)
    if not votes.results_visible(votes.get(c, vid)):
        return HTMLResponse("<p>Votul secret e în desfășurare: rezultatele și procesul-verbal apar după închidere.</p>")
    return render(request, "pv.html", admin, m=votes.minutes(c, vid),
                  generated=datetime.now(TZ).strftime("%d.%m.%Y %H:%M"))


# ---------- anunțuri / convocări ----------
@router.get("/anunturi", response_class=HTMLResponse)
def anunturi(request: Request, admin=Depends(current_admin)):
    c = ctx(request).conn
    items = [(n, notices.stats(c, n["id"])) for n in notices.list_all(c)]
    return render(request, "anunturi.html", admin, items=items, rows=members.rows(c))


@router.post("/anunturi")
async def anunturi_create(request: Request, kind: str = Form("anunt"), title: str = Form(...),
                          body: str = Form(""), row_label: str = Form(""), file: UploadFile | None = File(None),
                          admin=Depends(admin_post)):
    st = ctx(request)
    path = None
    if file is not None and file.filename:
        d = pathlib.Path(st.settings.upload_dir)
        d.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch for ch in pathlib.Path(file.filename).name if ch.isalnum() or ch in "._-") or "doc"
        p = d / f"{secrets.token_hex(4)}_{safe}"
        p.write_bytes(await file.read())
        path = str(p)
    nid = notices.create(st.conn, kind, title.strip(), body.strip(), row_label or None, path)
    audit.log(st.conn, admin["id"], kind + "_trimis", f"#{nid} {title.strip()} (rand: {row_label or 'toti'})")
    if st.bot:
        spawn(request, botmod.broadcast_notice(st.bot, st.conn, nid))
    return RedirectResponse("/anunturi", 303)


# ---------- cotizații ----------
@router.get("/cotizatii", response_class=HTMLResponse)
def cotizatii(request: Request, admin=Depends(current_admin)):
    c = ctx(request).conn
    return render(request, "cotizatii.html", admin, items=dues.list_all(c), s=dues.summary(c))


@router.post("/cotizatii/{due_id}/achitat")
def cotizatii_paid(due_id: int, request: Request, admin=Depends(admin_post)):
    dues.mark_paid(ctx(request).conn, due_id)
    audit.log(ctx(request).conn, admin["id"], "cotizatie_achitata", f"#{due_id}")
    return RedirectResponse("/cotizatii", 303)


# ---------- sesizări ----------
@router.get("/sesizari", response_class=HTMLResponse)
def sesizari(request: Request, admin=Depends(current_admin)):
    return render(request, "sesizari.html", admin, items=reports.list_all(ctx(request).conn))


@router.post("/sesizari/{rid}/rezolva")
def sesizari_resolve(rid: int, request: Request, admin=Depends(admin_post)):
    st = ctx(request)
    m = reports.resolve(st.conn, rid)
    audit.log(st.conn, admin["id"], "sesizare_rezolvata", f"#{rid}")
    if m and m["telegram_id"] and st.bot:
        spawn(request, botmod.notify(st.bot, m["telegram_id"], t(m["lang"], "report_done", id=rid)))
    return RedirectResponse("/sesizari", 303)


@router.get("/foto/{rid}")
async def foto(rid: int, request: Request, admin=Depends(current_admin)):
    st = ctx(request)
    r = reports.get(st.conn, rid)
    if not r or not r["photo_file_id"] or not st.bot:
        raise HTTPException(404)
    f = await st.bot.get_file(r["photo_file_id"])
    buf = await st.bot.download_file(f.file_path)
    return Response(buf.read(), media_type="image/jpeg")


@router.get("/voce/{rid}")
async def voce(rid: int, request: Request, admin=Depends(current_admin)):
    st = ctx(request)
    r = reports.get(st.conn, rid)
    if not r or not r["voice_file_id"] or not st.bot:
        raise HTTPException(404)
    f = await st.bot.get_file(r["voice_file_id"])
    buf = await st.bot.download_file(f.file_path)
    return Response(buf.read(), media_type="audio/ogg")
