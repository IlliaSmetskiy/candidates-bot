import gspread
from google.oauth2.service_account import Credentials
from config import SPREADSHEET_NAME, GOOGLE_CREDENTIALS_JSON, worksheet_names, NEW_STATE

def authenticate_google_sheets():
    creds = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    credentials = Credentials.from_service_account_info(
        creds,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    scope = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    """Аутентифікація та створення клієнта Google Sheets"""
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


