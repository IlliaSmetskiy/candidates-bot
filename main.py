import os
import asyncio
import httpx
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from fastapi import FastAPI, Request
import logging
import time
from contextlib import asynccontextmanager
from database import get_connection
from google_sheets import authenticate_google_sheets, fetch_sheet_data
from config import format_message, CHANNEL_ID, RAILWAY_DOMAIN
from messages import MESSAGES
# from Stripe_hosted.bot.middlewares.i18n import MyI18nMiddleware, i18n
from aiogram.types import BotCommand, BotCommandScopeDefault
from database import get_language_by_tg_id, set_language
from AI_text_paraphrasing import normalize
# -----------------------
# ENV
# -----------------------
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("No TELEGRAM_BOT_TOKEN in .env")

async def post_candidates(worksheet):
    client = authenticate_google_sheets()
    data, _, keys = fetch_sheet_data(client, worksheet)
    messages = normalize(data)
    if not messages:
        logging.info("Нема нових кандидатів")
        return
    for message in messages:
        if message:
            await bot.send_message(CHANNEL_ID,message)
    logging.info("Кандидатів запощено!")
    return 0

async def daily_post_candidates(worksheet: str, interval_hours: int = 24):
    # щоб не постити одразу при старті (опціонально)
    await asyncio.sleep(10)

    while True:
        try:
            logging.info("🔄 Перевіряю нових кандидатів")
            await post_candidates(worksheet)
        except Exception as e:
            logging.exception("❌ Помилка під час постингу кандидатів")

        # чекаємо 24 години (НЕ блокує loop)
        await asyncio.sleep(interval_hours * 60 * 60)

# -----------------------
# BOT
# -----------------------

bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
# dp.message.middleware(MyI18nMiddleware(i18n))


# -----------------------
# FASTAPI SERVER
# -----------------------

background_tasks_started = False
@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.delete_my_commands(scope=BotCommandScopeDefault())
    await bot.set_my_commands(
        commands=[
            BotCommand(command="subscribe", description="Subscribe to the channel WorkersEU"),
        ],
        scope=BotCommandScopeDefault()
    )
    global background_tasks_started

    if not background_tasks_started:
        asyncio.create_task(daily_post_candidates("Кандидати"))
        background_tasks_started = True
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def health():
    return {"status": "ok"}

@app.post("/telegram/webhook")
async def telegram_webhook(update: dict):
    if "message" not in update and "callback_query" not in update:
        return {"ok": True}
    telegram_update = Update.model_validate(update)
    await dp.feed_update(bot, telegram_update)
    return {"ok": True}

@app.post("/send-invite")
async def send_invite(request: Request):
    data = await request.json()
    expire_ts = data["expire_ts"]
    telegram_id = data["telegram_id"]
    invite = await bot.create_chat_invite_link( chat_id=CHANNEL_ID,
                                                expire_date=expire_ts,
                                                member_limit=1 )
    url = invite.invite_link
    conn = get_connection()
    lang = get_language_by_tg_id(conn, telegram_id) or "en"
    logging.info("Інвайт посилання створено")
    await bot.send_message(chat_id=telegram_id, text=MESSAGES["invite_link"][lang])
    logging.info("Інвайт посилання надіслано")
    return ({"status": "sent", "invite_link": url})

@app.post("/text-user")
async def text_user(request: Request):
    data = await request.json()
    mode = data["mode"]
    telegram_id = data["telegram_id"]
    conn = get_connection()
    lang = get_language_by_tg_id(conn, telegram_id) or "en"
    message = MESSAGES[mode][lang]
    await bot.send_message(chat_id=telegram_id, text=message)

@app.post("/cmd-send-payment-link")
async def cmd_send_payment_link(request: Request):
    data = await request.json()
    url = data["url"]
    telegram_id = data["telegram_id"]
    conn = get_connection()
    lang = get_language_by_tg_id(conn, telegram_id) or "en"
    await bot.send_message(chat_id=telegram_id, text=MESSAGES["payment_link"].format(url=url))
    return ({"status": "sent", "payment_link": url})

@app.post("/stop-sub")
async def stop_subscription(request: Request):
    data = await request.json()
    telegram_id = data["telegram_id"]
    conn = get_connection()
    lang = get_language_by_tg_id(conn, telegram_id) or "en"
    await bot.send_message(telegram_id, MESSAGES["subscription_stopped"][lang])
    await bot.ban_chat_member(
        chat_id= CHANNEL_ID,
        user_id= telegram_id,
        until_date=int(time.time()) + 30
    )
    await bot.unban_chat_member(
        chat_id= CHANNEL_ID,
        user_id= telegram_id,
        only_if_banned=True
    )
# -----------------------
# Aiogram Handlers
# -----------------------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    lang = message.from_user.language_code or "en"
    conn = get_connection()
    set_language(conn, lang, message.from_user.id)
    await message.answer(MESSAGES["start"][lang])

async def notify_server(payload, webhook):
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"https://illiagw.pythonanywhere.com/{webhook}",
            json=payload
        )

@dp.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message):
    conn = get_connection()
    telegram_id = message.from_user.id
    lang = get_language_by_tg_id(conn, telegram_id) or "en"
    asyncio.create_task(
        notify_server({
            "telegram_id": message.from_user.id
        }, "create-checkout-session")
    )

    await message.answer(MESSAGES["creating_payment_link"][lang])

@dp.message(Command("stop_subscription"))
async def cmd_stop_subscription(message: types.Message):
    asyncio.create_task(
        notify_server({
            "telegram_id": message.from_user.id
        }, "stop-subscription")
    )
    telegram_id = message.from_user.id
    conn = get_connection()
    lang = get_language_by_tg_id(conn, telegram_id) or "en"
    await message.answer(MESSAGES["subscription_stopped"][lang])
    await bot.ban_chat_member(
        chat_id= CHANNEL_ID,
        user_id= telegram_id,
        until_date=int(time.time()) + 30
    )
    await bot.unban_chat_member(
        chat_id= CHANNEL_ID,
        user_id= telegram_id,
        only_if_banned=True
    )




