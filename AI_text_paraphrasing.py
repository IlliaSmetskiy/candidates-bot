import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

PROMPT = """
You are a job-posting content normalizer.

Input: raw vacancy text from a Google Sheet.
It may contain:
- mixed languages
- internal comments
- informal phrases
- irrelevant notes

Your task:
1. Translate ALL to ukrainian
2. Remove any duplicates, internal or irrelevant comments
3. Rewrite the vacancy in a neutral, professional tone
4. Return STRICT string in ukrainian with fields:

👤Ім’я:
📱Контакти:
🎂Вік:
🪪Громадянство:
👷‍♀️Спеціалізація:
✅Досвід:
🗣Мови:
📄Документи:

If some info is missing, return empty string in that field.
Do NOT invent information.
Text to normalize:\n
"""

ALLOWED_FIELDS = ["person",
                  "phones",
                  "Вік",
                  "Громадянство",
                  "Спеціалізація",
                  "Досвід по спеціальності",
                  "Мови",
                  "Документ для виїзду за кордон"
                  ]

def normalize(data):
    client = OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    messages = []
    prompt = PROMPT
    for row in data:
        for key in row:
            if key in ALLOWED_FIELDS:
                prompt = prompt + str(key) + str(row[key])
        response = client.responses.create(
            input=prompt,
            model="openai/gpt-oss-20b",
        )
        prompt = PROMPT
        messages.append(response.output_text)
    return messages

