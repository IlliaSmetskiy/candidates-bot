import os
import asyncio
import httpx
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.types import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
    CallbackQuery, BotCommand, BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators)
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
router = Router()
dp.include_router(router)
# dp.message.middleware(MyI18nMiddleware(i18n))


# -----------------------
# FASTAPI SERVER
# -----------------------

background_tasks_started = False
@asynccontextmanager
async def lifespan(app: FastAPI):
    for sc in [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
        BotCommandScopeAllChatAdministrators(),
    ]:
        await bot.delete_my_commands(scope=sc)
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
    logging.info("UPDATE: %s", update.keys())
    if "callback_query" in update:
        logging.info("CALLBACK DATA: %s", update["callback_query"].get("data"))
    telegram_update = Update.model_validate(update)
    await dp.feed_webhook_update(bot=bot, update=telegram_update)
    return {"ok": True}

async def send_invite(data):
    expire_ts = data["expire_ts"]
    telegram_id = data["telegram_id"]
    invite = await bot.create_chat_invite_link( chat_id=CHANNEL_ID,
                                                expire_date=expire_ts,
                                                member_limit=1 )
    url = invite.invite_link
    conn = get_connection()
    try:
        lang = get_language_by_tg_id(conn, telegram_id) or "en"
    finally:
        conn.close()
    logging.info("Інвайт посилання створено")
    await bot.send_message(chat_id=telegram_id, text=MESSAGES["invite_link"][lang])
    logging.info("Інвайт посилання надіслано")
    return ({"status": "sent", "invite_link": url})

@app.post("/send-invite")
async def webhook_send_invite(request: Request):
    data = await request.json()
    asyncio.create_task(send_invite(data))
    return {"ok": 200}

async def text_user(data):
    mode = data["mode"]
    telegram_id = data["telegram_id"]
    conn = get_connection()
    try:
        lang = get_language_by_tg_id(conn, telegram_id) or "en"
    finally:
        conn.close()
    if mode == "checkout_session_is_pending":
        button = [[InlineKeyboardButton(text=MESSAGES['generate_anyway_button'][lang], callback_data="generate_payment_link_anyway")]]
        markup=InlineKeyboardMarkup(inline_keyboard=button)
        message = MESSAGES[mode][lang]
        await bot.send_message(chat_id=telegram_id, text=message, reply_markup=markup)
    else:
        message = MESSAGES[mode][lang]
        await bot.send_message(chat_id=telegram_id, text=message)

@app.post("/text-user")
async def webhook_text_user(request: Request):
    data = await request.json()
    asyncio.create_task(text_user(data))
    return {"ok": 200}

async def cmd_send_payment_link(data):
    url = data["url"]
    telegram_id = data["telegram_id"]
    conn = get_connection()
    try:
        lang = get_language_by_tg_id(conn, telegram_id) or "en"
    finally:
        conn.close()
    await bot.send_message(chat_id=telegram_id, text=MESSAGES["payment_link"][lang].format(url=url))
    return ({"status": "sent", "payment_link": url})

@app.post("/cmd-send-payment-link")
async def webhook_cmd_send_payment_link(request: Request):
    data = await request.json()
    asyncio.create_task(cmd_send_payment_link(data))
    return {"ok": 200}

async def stop_subscription(data):
    telegram_id = data["telegram_id"]
    conn = get_connection()
    try:
        lang = get_language_by_tg_id(conn, telegram_id) or "en"
    finally:
        conn.close()
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

@app.post("/stop-sub")
async def webhook_stop_subscription(request: Request):
    data = await request.json()
    asyncio.create_task(stop_subscription(data))
    return {"ok": 200}
# -----------------------
# Aiogram Handlers
# -----------------------

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    logging.info("Натиснуто кнопку старт")
    lang = message.from_user.language_code or "en"
    conn = get_connection()
    try:
        set_language(conn, lang, message.from_user.id)
    finally:
        conn.close()
    await message.answer(MESSAGES["start"][lang])

async def notify_server(payload, webhook):
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"https://admingw.pythonanywhere.com/{webhook}",
            json=payload
        )
@router.callback_query(F.data == "generate_payment_link_anyway")
async def generate_link_anyway(callback: CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    logging.info("Got Callback")
    telegram_id = callback.from_user.id
    conn = get_connection()
    try:
        lang = get_language_by_tg_id(conn, telegram_id) or "en"
    finally:
        conn.close()
    asyncio.create_task(
        notify_server({
            "telegram_id": telegram_id,
            "allow_new_payment": True,
        }, "create-checkout-session")
    )

    await  callback.message.answer(MESSAGES["creating_payment_link"][lang])
    

@router.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message, allow_new_payment=False):
    conn = get_connection()
    telegram_id = message.from_user.id
    try:
        lang = get_language_by_tg_id(conn, telegram_id) or "en"
    finally:
        conn.close()
    asyncio.create_task(
        notify_server({
            "telegram_id": telegram_id,
            "allow_new_payment": allow_new_payment
        }, "create-checkout-session")
    )

    await message.answer(MESSAGES["creating_payment_link"][lang])

@router.message(Command("stop_subscription"))
async def cmd_stop_subscription(message: types.Message):
    asyncio.create_task(
        notify_server({
            "telegram_id": message.from_user.id
        }, "stop-subscription")
    )
    telegram_id = message.from_user.id
    conn = get_connection()
    try:
        lang = get_language_by_tg_id(conn, telegram_id) or "en"
    finally:
        conn.close()
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
