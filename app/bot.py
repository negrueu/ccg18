import asyncio
import html
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, ChatJoinRequest, ChatMemberUpdated, FSInputFile,
                           InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove, WebAppInfo)

from . import auth, dues, members, notices, reports, votes
from . import settings_store as kv
from .i18n import t, menu_key

log = logging.getLogger("ccg18.bot")
TZ = ZoneInfo("Europe/Chisinau")
SEND_INTERVAL = 1 / 25  # sub limita globală de ~30 mesaje/s


class ReportFlow(StatesGroup):
    waiting = State()


class Onboard(StatesGroup):
    name = State()
    garages = State()


def needs_onboarding(m):
    return m is not None and m["status"] != "confirmat" and not m["self_name"]


def group_kb(conn, lang):
    link = kv.get(conn, "group_invite")
    if not link:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t(lang, "btn_group"), url=link)]])


def esc(s):
    return html.escape(s or "")


def fmt_dt(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return iso or ""


def now_local():
    return datetime.now(TZ).strftime("%d.%m.%Y %H:%M")


def garages_str(conn, mid):
    return ", ".join(g["number"] for g in members.garages(conn, mid)) or "-"


def main_menu(lang):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "m_votes")), KeyboardButton(text=t(lang, "m_dues"))],
                  [KeyboardButton(text=t(lang, "m_report")), KeyboardButton(text=t(lang, "m_news"))],
                  [KeyboardButton(text=t(lang, "m_mydata"))]],
        resize_keyboard=True, is_persistent=True)


def contact_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "btn_contact"), request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True)


def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇲🇩 Română", callback_data="lang:ro"),
        InlineKeyboardButton(text="Русский", callback_data="lang:ru")]])


def ballot_text(conn, v, m):
    lang = m["lang"]
    mode = (t(lang, "mode_garage", weight=votes.weight_for(conn, v, m["id"]))
            if v["weight_mode"] == "garage" else t(lang, "mode_member"))
    return t(lang, "ballot", title=esc(v["title"]), description=esc(v["description"]),
             deadline=fmt_dt(v["deadline"]), mode=mode, secret=t(lang, "secret") if v["secret"] else "")


def ballot_kb(v):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=o, callback_data=f"v:{v['id']}:{i}")] for i, o in enumerate(votes.options(v))])


async def safe_send(coro_factory, conn=None, telegram_id=None):
    """Trimite cu respectarea limitelor: 429 -> așteaptă retry_after; 403 (bot blocat) -> marchează și renunță."""
    for _ in range(3):
        try:
            res = await coro_factory()
            if conn is not None and telegram_id is not None:
                conn.execute("update members set blocked_at=null where telegram_id=?", (telegram_id,))
            return res
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            if conn is not None and telegram_id is not None:
                conn.execute("update members set blocked_at=datetime('now') where telegram_id=?", (telegram_id,))
            return None
        except TelegramBadRequest as e:
            log.warning("bad request: %s", e)
            return None
    return None


def build(conn, settings):
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    r = Router()
    r.message.filter(F.chat.type == "private")
    g = Router()  # evenimente de grup: cereri de aderare, statutul botului
    dp.include_router(r)
    dp.include_router(g)

    def me(user_id):
        return members.by_telegram(conn, user_id)

    async def after_login(msg: Message, state: FSMContext, m):
        lang = m["lang"]
        if needs_onboarding(m):
            await state.set_state(Onboard.name)
            await msg.answer(t(lang, "ask_name"), reply_markup=ReplyKeyboardRemove())
            return
        await msg.answer(t(lang, "welcome", name=esc(m["name"]), garages=garages_str(conn, m["id"])),
                         reply_markup=main_menu(lang))
        if not members.is_confirmed(m):
            await msg.answer(t(lang, "pending"))
        gk = group_kb(conn, lang)
        if gk:
            await msg.answer(t(lang, "group_join"), reply_markup=gk)
        for v in votes.open_for(conn, m["id"]):
            await msg.answer(ballot_text(conn, v, m), reply_markup=ballot_kb(v))

    # ---------- onboarding membri neconfirmați ----------
    @r.message(StateFilter(Onboard.name), F.text)
    async def ob_name(msg: Message, state: FSMContext):
        m = me(msg.from_user.id)
        name = " ".join(msg.text.split())
        if not m or len(name) < 3 or name.startswith("/"):
            await msg.answer(t(m["lang"] if m else "ro", "ask_name"))
            return
        await state.update_data(self_name=name[:120])
        await state.set_state(Onboard.garages)
        await msg.answer(t(m["lang"], "ask_garages"))

    @r.message(StateFilter(Onboard.garages), F.text)
    async def ob_garages(msg: Message, state: FSMContext):
        m = me(msg.from_user.id)
        if not m:
            await state.clear()
            return
        gs = ", ".join(x for x in msg.text.replace(";", ",").replace(" ", ",").split(",") if x.strip())
        if not gs:
            await msg.answer(t(m["lang"], "ask_garages"))
            return
        data = await state.get_data()
        await state.clear()
        members.set_self_info(conn, m["id"], data.get("self_name", ""), gs)
        await msg.answer(t(m["lang"], "onboard_done", name=esc(data.get("self_name", "")), garages=esc(gs)),
                         reply_markup=main_menu(m["lang"]))
        gk = group_kb(conn, m["lang"])
        if gk:
            await msg.answer(t(m["lang"], "group_join"), reply_markup=gk)

    # ---------- grupul Telegram: doar membri din registru ----------
    @g.chat_join_request()
    async def join_request(req: ChatJoinRequest):
        if str(req.chat.id) != str(kv.get(conn, "group_chat_id")):
            return
        m = me(req.from_user.id)
        if m:
            await req.approve()
            await safe_send(lambda: bot.send_message(req.user_chat_id, t(m["lang"], "join_ok")))
        else:
            await req.decline()
            await safe_send(lambda: bot.send_message(req.user_chat_id, t("ro", "join_need_start") + "\n\n" +
                                                     t("ru", "join_need_start")))

    @g.my_chat_member()
    async def bot_status(ev: ChatMemberUpdated):
        if ev.chat.type not in ("group", "supergroup"):
            return
        adder = me(ev.from_user.id)
        if ev.new_chat_member.status == "administrator" and adder and adder["is_admin"]:
            try:
                link = await bot.create_chat_invite_link(ev.chat.id, name="Membri CCG 18", creates_join_request=True)
            except Exception as e:
                log.warning("invite link: %s", e)
                return
            kv.put(conn, "group_chat_id", ev.chat.id)
            kv.put(conn, "group_title", ev.chat.title)
            kv.put(conn, "group_invite", link.invite_link)
            await safe_send(lambda: bot.send_message(
                adder["telegram_id"], f"✅ Grupul „{esc(ev.chat.title)}” e legat de bot. Link de aderare (cu verificare):"
                                      f"\n{link.invite_link}"))
        elif ev.new_chat_member.status in ("left", "kicked") and str(ev.chat.id) == str(kv.get(conn, "group_chat_id")):
            kv.put(conn, "group_invite", None)

    # ---------- intrare ----------
    @r.message(CommandStart())
    async def start(msg: Message, state: FSMContext):
        await state.clear()
        m = me(msg.from_user.id)
        if m and needs_onboarding(m):
            await after_login(msg, state, m)
            return
        if m:
            await msg.answer(t(m["lang"], "welcome", name=esc(m["name"]), garages=garages_str(conn, m["id"])),
                             reply_markup=main_menu(m["lang"]))
            return
        await msg.answer(t("ro", "choose_lang"), reply_markup=lang_kb())

    @r.callback_query(F.data.startswith("lang:"))
    async def choose_lang(cb: CallbackQuery, state: FSMContext):
        lang = cb.data.split(":")[1]
        await state.update_data(lang=lang)
        await cb.answer()
        m = me(cb.from_user.id)
        if m:
            members.set_lang(conn, m["id"], lang)
            await cb.message.answer(t(lang, "welcome", name=esc(m["name"]), garages=garages_str(conn, m["id"])),
                                    reply_markup=main_menu(lang))
        else:
            await cb.message.answer(t(lang, "ask_contact"), reply_markup=contact_kb(lang))

    @r.message(F.contact)
    async def got_contact(msg: Message, state: FSMContext):
        lang = (await state.get_data()).get("lang", "ro")
        c = msg.contact
        m = auth.login_contact(conn, msg.from_user.id, c.user_id, c.phone_number)
        if not m:
            log.info("contact respins: user=%s contact_user=%s phone=%s", msg.from_user.id, c.user_id,
                     c.phone_number)
            if c.user_id != msg.from_user.id:
                await msg.answer(t(lang, "not_own"), reply_markup=contact_kb(lang))
            else:
                await msg.answer(t(lang, "not_found", phone=esc(c.phone_number)), reply_markup=ReplyKeyboardRemove())
            return
        members.set_lang(conn, m["id"], lang)
        await after_login(msg, state, members.get(conn, m["id"]))

    @r.message(Command("limba"))
    async def change_lang(msg: Message):
        await msg.answer(t("ro", "choose_lang"), reply_markup=lang_kb())

    # ---------- panou ----------
    @r.message(Command("panou"))
    async def panel(msg: Message):
        m = me(msg.from_user.id)
        if not m or not m["is_admin"]:
            await msg.answer(t(m["lang"] if m else "ro", "not_admin"))
            return
        tok = auth.issue_admin_token(conn, m["id"])
        kb = None
        if settings.base_url.startswith("https://"):
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text=t(m["lang"], "btn_panel"), web_app=WebAppInfo(url=f"{settings.base_url}/app"))]])
        await msg.answer(t(m["lang"], "panel", url=f"{settings.base_url}/login/{tok}"), reply_markup=kb,
                         disable_web_page_preview=True)

    # ---------- vot ----------
    @r.callback_query(F.data.startswith("v:"))
    async def pick(cb: CallbackQuery):
        m = me(cb.from_user.id)
        if not m:
            await cb.answer(t("ro", "need_login"), show_alert=True)
            return
        _, vid, idx = cb.data.split(":")
        v = votes.get(conn, int(vid))
        if not v:
            await cb.answer(t(m["lang"], "err_not_found"), show_alert=True)
            return
        opts = votes.options(v)
        idx = int(idx)
        if not (0 <= idx < len(opts)):
            await cb.answer(t(m["lang"], "err_bad_option"), show_alert=True)
            return
        if votes.has_voted(conn, v["id"], m["id"]):
            await cb.answer(t(m["lang"], "err_already"), show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=t(m["lang"], "btn_confirm"), callback_data=f"c:{v['id']}:{idx}"),
            InlineKeyboardButton(text=t(m["lang"], "btn_back"), callback_data=f"b:{v['id']}")]])
        await cb.answer()
        await cb.message.edit_text(t(m["lang"], "confirm", option=esc(opts[idx])), reply_markup=kb)

    @r.callback_query(F.data.startswith("b:"))
    async def back(cb: CallbackQuery):
        m = me(cb.from_user.id)
        v = votes.get(conn, int(cb.data.split(":")[1]))
        await cb.answer()
        if m and v:
            await cb.message.edit_text(ballot_text(conn, v, m), reply_markup=ballot_kb(v))

    @r.callback_query(F.data.startswith("c:"))
    async def confirm(cb: CallbackQuery):
        m = me(cb.from_user.id)
        if not m:
            await cb.answer(t("ro", "need_login"), show_alert=True)
            return
        _, vid, idx = cb.data.split(":")
        v = votes.get(conn, int(vid))
        try:
            votes.cast(conn, int(vid), m["id"], int(idx))
        except votes.VoteError as e:
            await cb.answer(t(m["lang"], f"err_{e.code}"), show_alert=True)
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
            return
        await cb.answer("✅")
        key = "receipt_secret" if v["secret"] else "receipt"
        await cb.message.edit_text(t(m["lang"], key, title=esc(v["title"]), option=esc(votes.options(v)[int(idx)]),
                                     garages=garages_str(conn, m["id"]), time=now_local()))

    # ---------- convocare: confirmare primire ----------
    @r.callback_query(F.data.startswith("ack:"))
    async def ack(cb: CallbackQuery):
        m = me(cb.from_user.id)
        if not m:
            await cb.answer(t("ro", "need_login"), show_alert=True)
            return
        notices.ack(conn, int(cb.data.split(":")[1]), m["id"])
        await cb.answer(t(m["lang"], "ack_ok"), show_alert=True)
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass

    # ---------- sesizare ----------
    @r.message(Command("anulare"))
    async def cancel(msg: Message, state: FSMContext):
        await state.clear()
        m = me(msg.from_user.id)
        lang = m["lang"] if m else "ro"
        await msg.answer(t(lang, "cancelled"), reply_markup=main_menu(lang) if m else None)

    @r.message(StateFilter(ReportFlow.waiting), F.photo | F.text)
    async def report_body(msg: Message, state: FSMContext):
        m = me(msg.from_user.id)
        await state.clear()
        if not m:
            return
        if msg.text and menu_key(msg.text):
            await menu(msg, state)
            return
        photo = msg.photo[-1].file_id if msg.photo else None
        text = msg.caption if msg.photo else msg.text
        rid = reports.create(conn, m["id"], text or "", photo)
        await msg.answer(t(m["lang"], "report_ok", id=rid), reply_markup=main_menu(m["lang"]))
        for a in conn.execute("select * from members where is_admin=1 and telegram_id is not null"):
            body = t(a["lang"], "new_report_admin", id=rid, name=esc(m["name"]),
                     garages=garages_str(conn, m["id"]), text=esc(text or ""))
            if photo:
                await safe_send(lambda: bot.send_photo(a["telegram_id"], photo, caption=body[:1024]))
            else:
                await safe_send(lambda: bot.send_message(a["telegram_id"], body))

    # ---------- meniu ----------
    @r.message(F.text.func(lambda s: menu_key(s) is not None))
    async def menu(msg: Message, state: FSMContext):
        m = me(msg.from_user.id)
        if not m:
            await msg.answer(t("ro", "need_login"))
            return
        lang, key = m["lang"], menu_key(msg.text)
        if key == "m_votes":
            open_v = votes.open_for(conn, m["id"])
            if not open_v:
                await msg.answer(t(lang, "no_votes"))
            for v in open_v:
                await msg.answer(ballot_text(conn, v, m), reply_markup=ballot_kb(v))
        elif key == "m_dues":
            rows = dues.for_member(conn, m["id"])
            unpaid = [d for d in rows if not d["paid"]]
            lines = [t(lang, "dues_head")] + [
                t(lang, "dues_line", garage=d["garage"], period=d["period"], amount=d["amount"],
                  status=t(lang, "paid") if d["paid"] else t(lang, "unpaid")) for d in rows]
            if not unpaid:
                lines.append(t(lang, "dues_none"))
                await msg.answer("\n".join(lines))
                return
            total = sum(d["amount"] for d in unpaid)
            lines.append(t(lang, "dues_total", total=total))
            await msg.answer("\n".join(lines))
            png = dues.qr_png(garages_str(conn, m["id"]), total)
            await msg.answer_photo(BufferedInputFile(png, "qr-demo.png"), caption=t(lang, "qr_caption"))
        elif key == "m_report":
            await state.set_state(ReportFlow.waiting)
            await msg.answer(t(lang, "report_ask"), reply_markup=ReplyKeyboardRemove())
        elif key == "m_mydata":
            if not members.is_confirmed(m):
                name = m["self_name"] or m["name"]
                gs = m["self_garages"] or "-"
            else:
                name, gs = m["name"], garages_str(conn, m["id"])
            await msg.answer(t(lang, "mydata", name=esc(name), phone=m["phone"], garages=esc(gs),
                               status=t(lang, "st_confirmat" if members.is_confirmed(m) else "st_neconfirmat")))
            if not members.is_confirmed(m):
                await state.set_state(Onboard.name)
                await msg.answer(t(lang, "ask_name"))
        elif key == "m_news":
            items = notices.recent_for(conn, m["id"])
            if not items:
                await msg.answer(t(lang, "news_none"))
            for n in reversed(items):
                await msg.answer(notice_text(n, lang))

    @r.message()
    async def fallback(msg: Message):
        m = me(msg.from_user.id)
        if not m:
            await msg.answer(t("ro", "choose_lang"), reply_markup=lang_kb())
        else:
            await msg.answer(t(m["lang"], "welcome", name=esc(m["name"]), garages=garages_str(conn, m["id"])),
                             reply_markup=main_menu(m["lang"]))

    return bot, dp


def notice_text(n, lang):
    key = "notice_convocare" if n["kind"] == "convocare" else "notice_anunt"
    return t(lang, key, title=esc(n["title"]), body=esc(n["body"]))


async def broadcast_notice(bot, conn, nid) -> int:
    n = notices.get(conn, nid)
    sent = 0
    for m in [x for x in notices.recipients(conn, nid) if not x["blocked_at"]]:
        lang = m["lang"]
        kb = None
        if n["kind"] == "convocare":
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text=t(lang, "btn_ack"), callback_data=f"ack:{nid}")]])
        text = notice_text(n, lang)
        prot = n["kind"] == "convocare"
        tid = m["telegram_id"]
        if n["file_path"]:
            res = await safe_send(lambda: bot.send_document(
                tid, FSInputFile(n["file_path"]), caption=text[:1024], reply_markup=kb,
                protect_content=prot), conn, tid)
            if res and len(text) > 1024:
                await safe_send(lambda: bot.send_message(tid, text[:4096], protect_content=prot), conn, tid)
        else:
            res = await safe_send(lambda: bot.send_message(tid, text[:4096], reply_markup=kb,
                                                           protect_content=prot), conn, tid)
        if res:
            notices.mark_delivered(conn, nid, m["id"])
            sent += 1
        await asyncio.sleep(SEND_INTERVAL)
    return sent


async def broadcast_vote(bot, conn, vid) -> int:
    v = votes.get(conn, vid)
    sent = 0
    for m in conn.execute("select * from members where telegram_id is not null and blocked_at is null "
                          "and status='confirmat'").fetchall():
        if votes.has_voted(conn, vid, m["id"]):
            continue
        tid = m["telegram_id"]
        res = await safe_send(lambda: bot.send_message(
            tid, t(m["lang"], "new_vote") + "\n\n" + ballot_text(conn, v, m), reply_markup=ballot_kb(v)), conn, tid)
        if res:
            sent += 1
        await asyncio.sleep(SEND_INTERVAL)
    return sent


async def notify(bot, telegram_id, text):
    return await safe_send(lambda: bot.send_message(telegram_id, text))


async def setup_profile(bot):
    """Comenzi și descriere în ro/ru (setMyCommands cu language_code)."""
    from aiogram.types import BotCommand
    cmds = {
        "ro": [("start", "Pornire / meniu"), ("limba", "Schimbă limba"), ("anulare", "Anulează"),
               ("panou", "Panoul conducerii")],
        "ru": [("start", "Старт / меню"), ("limba", "Сменить язык"), ("anulare", "Отмена"),
               ("panou", "Панель правления")],
    }
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in cmds["ro"]])
    for lang, items in cmds.items():
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in items], language_code=lang)
    await bot.set_my_description(
        "Botul oficial al CCG 18 (Chișinău): voturi, anunțuri, convocări, cotizații și sesizări pentru membri. "
        "Apăsați Start și trimiteți numărul de telefon pentru identificare.")
    await bot.set_my_description("Бот ГСК 18 (Кишинёв): голосования, объявления, созывы собраний, взносы и "
                                 "обращения. Нажмите Старт и отправьте номер телефона.", language_code="ru")
    await bot.set_my_short_description("CCG 18 - voturi, anunțuri, cotizații")
