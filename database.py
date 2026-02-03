import mysql.connector
import datetime
from dateutil.relativedelta import relativedelta
import os
from urllib.parse import urlparse
from dotenv import load_dotenv
load_dotenv()
import logging
logging.basicConfig(level=logging.INFO)

def get_connection():
    url = urlparse(os.getenv("MYSQL_PUBLIC_URL"))

    return mysql.connector.connect(
        host=url.hostname,
        port=url.port,
        user=url.username,
        password=url.password,
        database="railway",
    )

ALLOWED_FIELDS = {
    "subscription_id",
    "subscription_active",
    "subscription_end"
    }

def set_language(conn, lang, tg_id):
    try:
        with conn.cursor() as cur:
            sql = """
                UPDATE users
                SET language = %s
                WHERE telegram_id = %s
            """
            cur.execute(sql, (lang, tg_id))
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e

def get_language_by_tg_id(conn, tg_id):
    with conn.cursor() as cur:
        sql = """
            SELECT language FROM users
            WHERE telegram_id = %s
        """
        cur.execute(sql, (tg_id,))
        lang = cur.fetchone()
        if lang:
            return lang[0]
        return "en"

def update_user_info(conn, params: dict, user_id: int):
    with conn.cursor() as cur:

        params = {
            k:v for k, v in params.items()
            if k in ALLOWED_FIELDS
        }

        if not params:
            return

        set_clause = ", ".join(f"{col} = %s" for col in params)

        values = list(params.values())

        sql = f"""
                UPDATE users
                SET {set_clause}
                WHERE telegram_id = %s
        """

        cur.execute(sql, values + [user_id])
        conn.commit()

def add_or_update_subscription(conn, telegram_id: int, subscription_id: str):
    with conn.cursor() as cur:
        sql = """
            INSERT INTO users (telegram_id, subscription_id)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE
                subscription_id = VALUES(subscription_id)
        """
        cur.execute(sql, (telegram_id, subscription_id))
        conn.commit()


# def add_user(conn, params: dict):

#     params = {
#         k:v for k, v in params.items()
#         if k in ALLOWED_FIELDS
#         }

#     columns = ", ".join(key for key in params)
#     playsholders = ", ".join("%s" for _ in range(len(params)))
#     values = list(params.values())

#     with conn.cursor() as cur:
#         sql = (f"""
#                 INSERT INTO users ({columns})
#                 VALUES ({playsholders})
#                 ON DUPLICATE KEY UPDATE
#                     subscription_id = %s;
#                 """)
#         cur.execute(sql, values + [subscription_id])
#         conn.commit()

def change_subscription(conn, subscription_active: bool, subscription_id: str):
    end_date = datetime.date.today() + relativedelta(months=1)

    with conn.cursor() as cur:
        cur.execute("""
            UPDATE users
            SET subscription_active = %s,
                subscription_end = %s
            WHERE subscription_id = %s
        """, (subscription_active, end_date, subscription_id))
        conn.commit()

def get_tg_id_by_sub_id(conn, sub_id):
    with conn.cursor() as cur:

        sql = """
            SELECT telegram_id FROM users
            WHERE subscription_id = %s
            """
        cur.execute(sql, (sub_id,))
        row = cur.fetchone()

    return row


