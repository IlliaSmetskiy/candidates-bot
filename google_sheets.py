import gspread
from google.oauth2.service_account import Credentials
from config import SPREADSHEET_NAME, CREDENTIALS_PATH, worksheet_names, NEW_STATE

scope = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def authenticate_google_sheets():
    """Аутентифікація та створення клієнта Google Sheets"""
    credentials = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scope)
    client = gspread.authorize(credentials)
    return client

def fetch_sheet_data(client, worksheet_name):
    """Отримати дані з Google Sheets"""
    spreadsheet = client.open(SPREADSHEET_NAME)
    worksheet = spreadsheet.worksheet(worksheet_name)
    data = worksheet.get_all_values()
    if not data:
        return [], "", []
    res = []

    keys = data[0]
    rows = data[1:]

    for row in rows:
        if not row:
            continue

        status = row[0].strip().lower()
        if status != NEW_STATE.lower():
            continue
        row += [""] * (len(keys) - len(row))

        res.append(dict(zip(keys, row)))

    return res, worksheet, keys


