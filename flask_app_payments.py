import os
import time
import stripe
import datetime
import traceback
import requests
import threading
from werkzeug.exceptions import HTTPException
from flask import Flask, request, jsonify, render_template, abort
from dotenv import load_dotenv
import logging
import mysql.connector
from database import add_or_update_subscription, get_tg_id_by_sub_id, change_subscription, get_connection, get_language_by_tg_id
from my_errors import UserNotFound, DatabaseError, InvalidRequest



logging.basicConfig(level=logging.INFO)

# load_dotenv("/home/adminGW/mysite/.env")
project_folder = os.path.expanduser('~/mysite')  # adjust as appropriate
load_dotenv(os.path.join(project_folder, '.env'))

# === ENV VARIABLES ===
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL")
PRICE_ID = os.getenv("STRIPE_PRICE_ID")  # optional fixed price ID
BOT_URL = os.getenv("BOT_URL")

if not STRIPE_SECRET_KEY:
    raise RuntimeError("STRIPE_SECRET_KEY not set")

stripe.api_key = STRIPE_SECRET_KEY


def has_active_or_pending_session(telegram_id, allow=False):
    if allow:
        return False
    sessions = stripe.checkout.Session.list(
        limit=5,
        expand=["data.subscription"]
    )

    for s in sessions.data:
        # перевіряємо метадані
        if s.metadata.get("telegram_id") == str(telegram_id):
            # перевірка статусу сесії
            if s.payment_status in ["unpaid", "no_payment_required"]:
                return True
    return False

def notify_bot(telegram_id, mode):
    try:
        requests.post(
            BOT_URL + "/text-user",
            json={
                "telegram_id": str(telegram_id),
                "mode": mode
            },
            timeout=10
        )
    except Exception as e:
        logging.warning(f"Failed to notify bot: {e}")

def check_for_ended_subscriptions():
    try:
        conn = get_connection()
        today = datetime.date.today()
        with conn.cursor() as cur:
            select_sql = """
                SELECT telegram_id
                FROM users
                WHERE subscription_end < %s
                AND subscription_active = %s
            """
            cur.execute(select_sql, (today, True))
            users = cur.fetchall()

            if not users:
                return []

            update_sql = """
                UPDATE users
                SET subscription_active = %s
                WHERE subscription_end < %s
                AND subscription_active = %s
            """
            cur.execute(update_sql, (False, today, True))
            conn.commit()

            return [u["telegram_id"] for u in users]

    except Exception:
        logging.exception("Failed to check ended subscriptions")
        return []

# Flask app
app = Flask(__name__)

# ====== LOG ALL ERRORS ======
@app.errorhandler(Exception)
def handle_error(e):
    # Якщо це "нормальний" HTTPException (404, 405, тощо) — віддай як є
    if isinstance(e, HTTPException):
        print(f"\n⚠️ HTTPException {e.code} on {request.method} {request.path}")
        return e

    print("\n❌ SERVER ERROR")
    print(f"Request: {request.method} {request.path}")
    traceback.print_exc()
    return jsonify({"error": "Internal server error"}), 500

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "alive"})

# @app.route("/config", methods=["GET"])
# def config():
#     return {"public_key": os.getenv("STRIPE_PUBLIC_KEY")}

# @app.route('/pay')
# def pay():
#     return render_template("payment.html")

@app.route('/stop-subscription', methods=["POST"])
def stop_subscription():

    telegram_id = None

    try:
        data = request.get_json()
        telegram_id = data.get("telegram_id")
        if not telegram_id:
            raise InvalidRequest()

        conn = get_connection()
        with conn.cursor() as cur:
            sql = """
                UPDATE users
                SET subscription_active = %s
                WHERE telegram_id = %s
            """
            cur.execute(sql, (False, telegram_id))
            conn.commit()

            if cur.rowcount == 0:
                raise UserNotFound()

    except DatabaseError:
        logging.info("Db connection error")
        mode = "subscription_stop_error"
        notify_bot(telegram_id, mode)
        return jsonify({"error": "Database error"}), 500

    except mysql.connector.Error:
        mode = "subscription_stop_error"
        notify_bot(telegram_id, mode)
        return jsonify({"error": "Database error"}), 500

    except InvalidRequest:
        logging.info("Request error")
        mode = "subscription_stop_error"
        if telegram_id:
            notify_bot(telegram_id, mode)
        return jsonify({"error": "Invalid request"}), 400

    except UserNotFound:
        logging.warning("User not found")
        mode = "subscription_stop_error"
        notify_bot(telegram_id, mode)
        return jsonify({"error": "User not found"}), 404

    notify_bot(telegram_id, "subscription_stopped")
    return jsonify({"status": "subscription stopped"}), 200



# ========== CREATE CHECKOUT SESSION ==========
@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():

    try:
        data = request.get_json()
        telegram_id = data.get("telegram_id")
        message_id = data.get("message_id")
        allow_new_payment = data.get("allow_new_payment")
        if not telegram_id:
            return jsonify({"error": "telegram_id required"}), 400

        if has_active_or_pending_session(telegram_id, allow_new_payment):
                notify_bot(telegram_id, "checkout_session_is_pending")
                return "", 200

        conn = get_connection()
        lang = get_language_by_tg_id(conn, telegram_id)
        conn.close()
        # with conn.cursor() as cur:
        #     select_sql = """
        #         SELECT telegram_id
        #         FROM users
        #         WHERE telegram_id = %s
        #         AND subscription_active = %s
        #     """
        #     cur.execute(select_sql, (telegram_id, True))
        #     user = cur.fetchone()
        #     if user:
        #         notify_bot(telegram_id, "subscription_is_already_active")
        #         return "", 200

        if PRICE_ID:
            # Using a STRIPE PRICE ID
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{'price': PRICE_ID, 'quantity': 1}],
                mode='payment',
                success_url=f"{BACKEND_BASE_URL}/templates/success/{lang}?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{BACKEND_BASE_URL}/templates/cancel/{lang}",
                metadata={'telegram_id': str(telegram_id)}
            )
        requests.post(
            BOT_URL + "/cmd-send-payment-link",
            json = {"url": session.url, "id": session.id, "telegram_id": str(telegram_id)},
            timeout = 20
            )
        return jsonify({"url": session.url, "telegram_id": str(telegram_id), "id": session.id})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ========== STRIPE WEBHOOK ==========
@app.route("/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        return jsonify({"error": "Invalid signature"}), 400
    except Exception:
        return jsonify({"error": "Invalid payload"}), 400

    logging.info(f"✅✅✅{event['type']}")

    try:
        conn = get_connection()
    except Exception as e:
        logging.info(f"‼️Error connecting to database: {e}")
    telegram_id = 0
    if event['type'] == 'checkout.session.completed':
        logging.info("✅ Checkout session completed")

        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        telegram_id = metadata.get("telegram_id")
        subscription_id = session["subscription"]

        add_or_update_subscription(conn, telegram_id, subscription_id)

    if event["type"] == "payment_intent.succeeded":
        logging.info(f"invoice is paid")

        invoice = event["data"]["object"]
        sub_id = invoice["lines"]["data"][0]["parent"]["subscription_item_details"]["subscription"]
        logging.info(f"sub_id: {sub_id}")

        try:
            for attempt in range(4):
                conn = get_connection()
                row = get_tg_id_by_sub_id(conn, sub_id)
                if row:
                    break
                logging.info("Error: no user with such subscription id")
                logging.info(f"Row {row}")
                delay = 0.25 * (2 ** attempt)
                time.sleep(delay)

            tg_id = row[0]
            change_subscription(conn, True, sub_id)
            expire_ts = int(
                (datetime.datetime.utcnow() + datetime.timedelta(hours=24)).timestamp()
            )

            requests.post(
                BOT_URL + "/send-invite",
                json = {"telegram_id":tg_id, "expire_ts":expire_ts},
                timeout = 20
                )

        except Exception as e:
            logging.info(f"❌ Failed to create/send invite:{e}")

    if event['type'] == 'payment_intent.payment_failed':

        try:
            notify_bot(telegram_id, "payment_failed")
        except Exception as e:
            logging.info("❌ Failed to cssnd  request to the bot", e)
        logging.info("‼️ Failed to get paiment")

# ========== SUCCESS ==========
@app.route("/templates/success/<lang>", methods=['GET'])
def success_lang(lang):
    allowed = {"en", "ru", "uk"}
    if lang not in allowed:
        abort(404)
    return render_template(f"success{lang}.html")


# ========== CANCEL ==========
@app.route("/templates/cancel/<lang>")
def cancel_lang(lang):
    allowed = {"en", "ru", "uk"}
    if lang not in allowed:
        abort(404)
    return render_template(f"cancel{lang}.html")


def main():
    while True:
        ended_users = check_for_ended_subscriptions()
        for tg_id in ended_users:
            try:
                requests.post(
                    BOT_URL + "/stop-sub",
                    json={
                        "telegram_id": str(tg_id),
                    },
                    timeout=10
                )
            except Exception as e:
                logging.warning(f"Failed to notify bot about ended subscriptions: {e}")

        time.sleep(24 * 60 * 60)


# ========== MAIN ==========
if __name__ == "__main__":
    thread = threading.Thread(target=main, daemon=True)
    thread.start()

    app.run()
