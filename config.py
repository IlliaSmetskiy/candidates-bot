import os
from dotenv import load_dotenv


load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME")

CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH")

CHANNEL_ID = os.getenv("CHANNEL_ID")

worksheet_names = ["Кандидати"]

NEW_STATE = "Новий"
def format_message(row):
    f"""
    Ім’я: {row["person"]}
    Вік: {row["Вік"]}
    Громадянство: {row[2]}
    Місцезнаходження: {row[3]}
    Документи: {row[4]}
    Досвід: {row[5]}
    Шукає роботу: {row[6]}
    Мови: {row[7]}
    Водійські права: {row[8]}
    Контакт: {row["phones"]}
    """
    # message = ""
    # for k in row:
    #         message = message + k + row[k] + "\n"
    return message
