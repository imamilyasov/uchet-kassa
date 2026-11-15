import os
import json
import time
import hmac
import base64
import hashlib
import secrets

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from pymongo import MongoClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")


def load_env_file():
    """Читает .env, не перезаписывая уже заданные переменные окружения."""
    if not os.path.exists(ENV_PATH):
        return
    values = {}
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key, value in values.items():
        os.environ.setdefault(key, value)


load_env_file()


def ensure_auth_config():
    """Если пароль не задан, создаёт .env со случайным паролем."""
    if os.getenv("APP_USERS") or os.getenv("APP_PASSWORD"):
        return

    password = secrets.token_urlsafe(10)
    secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)

    lines = [
        "",
        "# Данные для входа. Пароль можно изменить на свой.",
        "APP_USERNAME=friend",
        f"APP_PASSWORD={password}",
        f"SECRET_KEY={secret_key}",
        "COOKIE_SECURE=0",
        "",
    ]
    with open(ENV_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    os.environ["APP_USERNAME"] = "friend"
    os.environ["APP_PASSWORD"] = password
    os.environ["SECRET_KEY"] = secret_key

    print("=" * 60)
    print("Создан файл .env с данными для входа.")
    print("Логин:  friend")
    print(f"Пароль: {password}")
    print("Пароль можно посмотреть/изменить в файле .env")
    print("=" * 60)


ensure_auth_config()

APP_USERNAME = os.getenv("APP_USERNAME", "friend")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
APP_USERS = os.getenv("APP_USERS", "")
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
COOKIE_NAME = "bazar_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 дней
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0").lower() in ("1", "true", "yes", "on")

STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(
    title="Базар Учет — Мобильное Приложение",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Если задан APP_USERS, формат: "user1:pass1,user2:pass2".
# Иначе используется одна пара APP_USERNAME / APP_PASSWORD.
def get_users():
    users = {}
    if APP_USERS:
        for pair in APP_USERS.split(","):
            if ":" in pair:
                username, password = pair.split(":", 1)
                username = username.strip()
                password = password.strip()
                if username and password:
                    users[username] = password
    if not users and APP_PASSWORD:
        users[APP_USERNAME] = APP_PASSWORD
    return users


def sign(value: str) -> str:
    return hmac.new(SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


def create_session(username: str) -> str:
    payload = {
        "u": username,
        "exp": int(time.time()) + SESSION_MAX_AGE,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{encoded}.{sign(encoded)}"


def read_session(token: Optional[str]) -> Optional[str]:
    if not token or "." not in token:
        return None
    try:
        encoded, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, sign(encoded)):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        username = payload.get("u")
        return username if username in get_users() else None
    except Exception:
        return None


def get_current_user(request: Request) -> Optional[str]:
    return read_session(request.cookies.get(COOKIE_NAME))


def require_user(request: Request) -> str:
    username = get_current_user(request)
    if not username:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return username


def set_session_cookie(response, username: str):
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session(username),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


class LoginData(BaseModel):
    username: str
    password: str


class ExpenseItem(BaseModel):
    product_name: str
    quantity_kg: float
    buy_price: float


class DailyReport(BaseModel):
    report_date: str
    expenses: List[ExpenseItem]
    total_revenue: float
    notes: Optional[str] = None


MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URL)
db = client["bazar_app_db"]
reports_collection = db["reports"]


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=303)
    with open(os.path.join(BASE_DIR, "login.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.post("/auth/login")
def login(data: LoginData):
    users = get_users()
    expected = users.get(data.username, "")
    password_ok = bool(expected) and hmac.compare_digest(expected, data.password)

    if not password_ok:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    response = JSONResponse({"ok": True, "username": data.username})
    set_session_cookie(response, data.username)
    return response


@app.post("/auth/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/auth/me")
def me(request: Request, username: str = Depends(require_user)):
    return {"username": username}


@app.get("/", response_class=HTMLResponse)
def get_ui(request: Request):
    if not get_current_user(request):
        return RedirectResponse("/login", status_code=303)
    with open(os.path.join(BASE_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.get("/manifest.json")
def get_manifest():
    return FileResponse(os.path.join(BASE_DIR, "manifest.json"), media_type="application/json")


@app.post("/reports/")
def save_or_update_report(report: DailyReport, username: str = Depends(require_user)):
    total_spent = round(sum(item.quantity_kg * item.buy_price for item in report.expenses), 2)
    net_profit = round(report.total_revenue - total_spent, 2)

    report_data = {
        "date": report.report_date,
        "expenses": [item.dict() for item in report.expenses],
        "total_spent": total_spent,
        "total_revenue": round(report.total_revenue, 2),
        "net_profit": net_profit,
        "notes": report.notes,
    }

    reports_collection.update_one(
        {"date": report.report_date},
        {"$set": report_data},
        upsert=True,
    )

    return {"status": "success", "data": report_data}


@app.get("/reports/")
def get_all_reports(username: str = Depends(require_user)):
    return list(reports_collection.find({}, {"_id": 0}))


@app.delete("/reports/{report_date}")
def delete_report(report_date: str, username: str = Depends(require_user)):
    result = reports_collection.delete_one({"date": report_date})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Отчет не найден")
    return {"status": "deleted"}
