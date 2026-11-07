import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
from pymongo import MongoClient

app = FastAPI(title="Базар Учет — Мобильное Приложение")

# Правильно подключаем папку static для картинок и иконки
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

client = MongoClient("mongodb://localhost:27017/")
db = client["bazar_app_db"]
reports_collection = db["reports"]

class ExpenseItem(BaseModel):
    product_name: str
    quantity_kg: float
    buy_price: float

class DailyReport(BaseModel):
    report_date: str  # Принимаем строку, чтобы избежать проблем с конвертацией дат
    expenses: List[ExpenseItem]
    total_revenue: float
    notes: Optional[str] = None

@app.get("/", response_class=HTMLResponse)
def get_ui():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/manifest.json")
def get_manifest():
    return FileResponse("manifest.json", media_type="application/json")

@app.post("/reports/")
def save_or_update_report(report: DailyReport):
    total_spent = sum(item.quantity_kg * item.buy_price for item in report.expenses)
    net_profit = report.total_revenue - total_spent
    
    report_data = {
        "date": report.report_date,
        "expenses": [item.dict() for item in report.expenses],
        "total_spent": total_spent,
        "total_revenue": report.total_revenue,
        "net_profit": net_profit,
        "notes": report.notes
    }
    
    # Жестко обновляем или создаем документ по строковому полю date
    reports_collection.update_one(
        {"date": report.report_date},
        {"$set": report_data},
        upsert=True
    )
    
    return {"status": "success", "data": report_data}

@app.get("/reports/")
def get_all_reports():
    return list(reports_collection.find({}, {"_id": 0}))

# Эндпоинт для удаления отчета по дате
@app.delete("/reports/{report_date}")
def delete_report(report_date: str):
    result = reports_collection.delete_one({"date": report_date})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Отчет не найден")
    return {"status": "deleted"}