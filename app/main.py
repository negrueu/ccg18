import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import pathlib

from . import config, db, seed
from . import bot as botmod
from .web import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ccg18")


def create_app(settings=None, conn=None) -> FastAPI:
    settings = settings or config.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state
        st.settings = settings
        st.conn = conn or db.connect(settings.db_path)
        st.tasks = set()
        st.bot = st.dp = None
        st.webhook_secret = None
        if settings.seed_demo:
            seed.run(st.conn, settings.admin_phones)
        else:
            seed.run_admins(st.conn, settings.admin_phones)
        poll_task = None
        if settings.run_bot and settings.bot_token:
            st.bot, st.dp = botmod.build(st.conn, settings)
            try:
                await botmod.setup_profile(st.bot)
            except Exception as e:  # profilul nu e critic
                log.warning("setup_profile: %s", e)
            if settings.base_url.startswith("https://"):
                st.webhook_secret = secrets.token_urlsafe(32)
                await st.bot.set_webhook(f"{settings.base_url}/tg/webhook", secret_token=st.webhook_secret,
                                         allowed_updates=st.dp.resolve_used_update_types())
                log.info("webhook setat pe %s/tg/webhook", settings.base_url)
            else:
                await st.bot.delete_webhook()
                poll_task = asyncio.create_task(st.dp.start_polling(st.bot, handle_signals=False))
                log.info("polling pornit (mod local)")
        yield
        if poll_task:
            await st.dp.stop_polling()
            poll_task.cancel()
        if st.bot:
            await st.bot.session.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(pathlib.Path(__file__).parent / "static")), name="static")
    app.include_router(router)

    @app.post("/tg/webhook")
    async def webhook(request: Request):
        st = request.app.state
        if not st.bot or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != st.webhook_secret:
            return JSONResponse({"ok": False}, 401)
        from aiogram.types import Update
        update = Update.model_validate(await request.json(), context={"bot": st.bot})
        task = asyncio.create_task(st.dp.feed_update(st.bot, update))
        st.tasks.add(task)
        task.add_done_callback(st.tasks.discard)
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        if exc.status_code == 401:
            return HTMLResponse(
                "<meta name=viewport content='width=device-width'><p style='font-family:sans-serif;padding:2rem'>"
                "Intrați din bot: trimiteți <b>/panou</b> lui @ccg18bot.</p>", 401)
        return HTMLResponse(f"<p>Eroare {exc.status_code}</p>", exc.status_code)

    return app


app = create_app()
