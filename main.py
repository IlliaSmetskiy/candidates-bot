import os
import asyncio
import httpx
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
    CallbackQuery, BotCommand, BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators, BotCommandScopeChat,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove)

from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.formatting import Url
from fastapi import FastAPI, Request
import logging
import time
from contextlib import asynccontextmanager
from database import get_connection
from google_sheets import authenticate_google_sheets, fetch_sheet_data
from config import format_message, CHANNEL_ID, RAILWAY_DOMAIN, CUSTOMER_PORTAL_URL, ADMIN_ID
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
async def get_language_from_db(telegram_id):
    conn = get_connection()
    try:
        lang = get_language_by_tg_id(conn, telegram_id) or "en"
    finally:
        conn.close()
    return lang
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
            BotCommand(command="language", description="Change language"),
            BotCommand(command="manage_subscription", description="Stop subscription"),
            BotCommand(command="help", description="Commands, admin contacts"),
            BotCommand(command="post", description="Post your profile"),
        ],
        scope=BotCommandScopeDefault()
    )
    await bot.set_my_commands(
        commands=[
            BotCommand(command="admin_post", description="Verifying the candidate's form and posting it"),
        ],
        scope=BotCommandScopeChat(chat_id=ADMIN_ID)
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
    lang = await get_language_from_db(telegram_id)
    logging.info("Інвайт посилання створено")
    logging.info(expire_ts)
    await bot.send_message(chat_id=telegram_id, text=MESSAGES["invite_link"][lang].format(url=url))
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
    lang = await get_language_from_db(telegram_id)
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
    lang = await get_language_from_db(telegram_id)
    await bot.send_message(chat_id=telegram_id, text=MESSAGES["payment_link"][lang].format(url=url))
    return ({"status": "sent", "payment_link": url})

@app.post("/cmd-send-payment-link")
async def webhook_cmd_send_payment_link(request: Request):
    data = await request.json()
    asyncio.create_task(cmd_send_payment_link(data))
    return {"ok": 200}

async def stop_subscription(data):
    telegram_id = data["telegram_id"]
    lang = await get_language_from_db(telegram_id)
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
        for attempt in range(4):
            try:
                r = await client.post(
                    f"https://admingw.pythonanywhere.com/{webhook}",
                    json=payload
                )
                r.raise_for_status()
                return
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError) as e:
                wait_time = 0.5 * (2 ** attempt)
                logging.error("notify_server network error (%s), retry in %.1fs", repr(e), wait_time)
                await asyncio.sleep(wait_time)

@router.message(Command("language"))
async def cmd_language(message: types.Message):
    logging.info("Натиснуто кнопку language")
    telegram_id = message.from_user.id
    lang = await get_language_from_db(telegram_id)
    keyboard = [[InlineKeyboardButton(text="Українська", callback_data="uk")],
                [InlineKeyboardButton(text="English", callback_data="en")],
                [InlineKeyboardButton(text="Русский", callback_data="ru")]]
    markup =  InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer(text=MESSAGES["commands"]["language"][lang], reply_markup=markup)

@router.message(Command("manage_subscription"))
async def cmd_manage(message: types.Message):
    logging.info("Натиснуто кнопку manage_subscription")
    telegram_id = message.from_user.id
    lang = await get_language_from_db(telegram_id)
    await bot.send_message(chat_id=telegram_id, text=MESSAGES["manage_subscription"][lang].format(url=CUSTOMER_PORTAL_URL))

@router.callback_query(F.data.in_({"uk", "en", "ru"}))
async def user_set_language(callback: CallbackQuery):
    try:
        await callback.answer()
    except TelegramBadRequest as e:
        if "query is too old" not in str(e):
            raise
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    lang = callback.data
    telegram_id = callback.from_user.id
    conn = get_connection()
    try:
        set_language(conn, lang, telegram_id)
    finally:
        conn.close()
    await callback.message.answer(text=MESSAGES["lang_changed"][lang])

@router.callback_query(F.data == "generate_payment_link_anyway")
async def generate_link_anyway(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    logging.info("Got Callback")
    telegram_id = callback.from_user.id
    lang = await get_language_from_db(telegram_id)
    asyncio.create_task(
        notify_server({
            "telegram_id": telegram_id,
            "allow_new_payment": True,
        }, "create-checkout-session")
    )

    await  callback.message.answer(MESSAGES["creating_payment_link"][lang])
    

@router.message(Command("subscribe"))
async def cmd_subscribe(message: types.Message, allow_new_payment=False):
    telegram_id = message.from_user.id
    lang = await get_language_from_db(telegram_id)

    member = await bot.get_chat_member(CHANNEL_ID, telegram_id)
    if member and member.status in ("member", "administrator", "creator"):
        await bot.send_message(chat_id=telegram_id, text=MESSAGES["subscription_is_already_active"][lang])
        logging.info(f"User {telegram_id} already has a subscription")
        return

    asyncio.create_task(
        notify_server({
            "telegram_id": telegram_id,
            "allow_new_payment": allow_new_payment
        }, "create-checkout-session")
    )

    await message.answer(MESSAGES["creating_payment_link"][lang])

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    telegram_id = message.from_user.id
    lang = await get_language_from_db(telegram_id)
    await message.answer(text=MESSAGES["help"][lang])

class PostForm(StatesGroup):
    waiting_for_text = State()

@router.message(Command("post"))
async def cmd_post_resume(message: types.Message, state: FSMContext):
    telegram_id = message.from_user.id
    lang = await get_language_from_db(telegram_id)
    await message.answer(text=MESSAGES["post"][lang])
    await state.set_state(PostForm.waiting_for_text)

@router.message(PostForm.waiting_for_text)
async def save_text(message: types.Message, state: FSMContext):
    await state.update_data(waiting_for_text=message.text)
    telegram_id = message.from_user.id
    lang = await get_language_from_db(telegram_id)

    data = await state.get_data()
    post_text = str(data.get("waiting_for_text", ""))
    post_text = ("📗 Анкета від користувача" + message.from_user.username + "\n" + "User ID: " +
                 str(telegram_id) + "\n" + post_text)

    if not post_text:
        await message.answer(text=MESSAGES["no_post_info_id_send"][lang])
        return
    await message.answer(text=MESSAGES["form_is_under_revision"][lang], reply_markup=ReplyKeyboardRemove())
    await state.clear()
    await bot.send_message(chat_id=ADMIN_ID, text=post_text)


# @router.message(Command("admin_post"), F.from_user.id == ADMIN_ID) #maybe for future
# async def cmd_admin_post(message: types.Message):


# @router.message(PostForm.waiting_for_text, F.text.in_(["Опублікувати", "Publish", "Опубликовать"]))
# async def send_post_text_to_admin(message: types.Message, state: FSMContext):
#     data = await state.get_data()
#     post_text = str(data)
#     telegram_id = message.from_user.id
#     post_text = "📗 Анкета від користувача\n" + post_text + "\n" + "Від користувача:\n" + str(telegram_id)
#     lang = await get_language_from_db(telegram_id)
#
#     if not post_text:
#         await message.answer(text=MESSAGES["no_post_info_id_send"][lang])
#         return
#     await message.answer(text=MESSAGES["form_is_under_revision"][lang], reply_markup=ReplyKeyboardRemove())
#     await state.clear()
#     await bot.send_message(chat_id=ADMIN_ID, text=post_text)


@router.message(Command("stop_subscription"))
async def cmd_stop_subscription(message: types.Message):
    asyncio.create_task(
        notify_server({
            "telegram_id": message.from_user.id
        }, "stop-subscription")
    )
    telegram_id = message.from_user.id
    lang = await get_language_from_db(telegram_id)
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
