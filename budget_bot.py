#!/usr/bin/env python3
# budget_bot.py — Bot Telegram quản lý thu chi đơn giản
# Cú pháp nhập tự nhiên: <tài khoản> <thu/chi> <số tiền> <ghi chú>
# Ví dụ: vietin chi 10k ăn sáng | momo thu 200k khách chuyển

import os
import re
import sqlite3
import threading
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)

# === Cấu hình ===
DB_PATH = os.environ.get("BUDGET_DB", "budget.db")
TOKEN = "8353974707:AAEvDloYhWQch5RvFtGlho612AKNr0ow0PM"  # ⚠️ điền token thật từ @BotFather

# === Kết nối SQLite ===
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
db_lock = threading.Lock()

def init_db():
    with db_lock:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('income','expense')),
            category TEXT,
            note TEXT,
            created_at TEXT NOT NULL
        )
        """)
        conn.commit()

def add_transaction(user_id: int, amount: float, ttype: str, category: str = "", note: str = ""):
    now = datetime.utcnow().isoformat()
    with db_lock:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO transactions (user_id, amount, type, category, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, amount, ttype, category, note, now)
        )
        conn.commit()
        return cur.lastrowid

def list_transactions(user_id: int, limit: int = 20):
    with db_lock:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, amount, type, category, note, created_at FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        )
        return cur.fetchall()

def report_month(user_id: int, year: int, month: int):
    start = date(year, month, 1)
    end = date(year + (month // 12), (month % 12) + 1, 1)
    start_iso = datetime(start.year, start.month, start.day).isoformat()
    end_iso = datetime(end.year, end.month, end.day).isoformat()
    with db_lock:
        cur = conn.cursor()
        cur.execute("""
            SELECT type, SUM(amount) FROM transactions
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY type
        """, (user_id, start_iso, end_iso))
        totals = {row[0]: row[1] for row in cur.fetchall()}
        cur.execute("""
            SELECT type, category, SUM(amount) FROM transactions
            WHERE user_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY type, category
            ORDER BY SUM(amount) DESC
        """, (user_id, start_iso, end_iso))
        by_cat = cur.fetchall()
    return totals, by_cat

# === Bộ phân tích cú pháp nhập tự nhiên ===
def parse_free_text(text: str):
    """
    Phân tích tin nhắn kiểu:
    vietin chi 10k ăn sáng
    momo thu 200k khách chuyển
    """
    text = text.lower().strip()

    # xác định loại
    if " chi " in f" {text} ":
        ttype = "expense"
    elif " thu " in f" {text} ":
        ttype = "income"
    else:
        return None

    # tài khoản (từ đầu đến trước từ 'chi' hoặc 'thu')
    acc = text.split("chi")[0].split("thu")[0].strip().split()[0]

    # tìm số tiền
    money_pattern = r"(\d+([.,]?\d+)?)(k|nghìn|ngàn|tr|triệu)?"
    m = re.search(money_pattern, text)
    if not m:
        return None
    amount = float(m.group(1))
    unit = m.group(3)
    if unit:
        if unit.startswith("k") or unit.startswith("ng"):
            amount *= 1000
        elif unit.startswith("tr"):
            amount *= 1_000_000

    # phần còn lại sau số tiền là ghi chú
    after_money = text[m.end():].strip()
    note = after_money if after_money else ""

    return {
        "account": acc,
        "type": ttype,
        "amount": amount,
        "note": note
    }

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 Xin chào! Mình là bot quản lý thu chi.\n\n"
        "Bạn có thể nhập nhanh như sau:\n"
        "• vietin chi 10k ăn sáng\n"
        "• momo thu 200k khách chuyển\n\n"
        "Hoặc dùng lệnh:\n"
        "/list — xem danh sách giao dịch\n"
        "/report — xem báo cáo tháng\n"
        "/quick — thêm nhanh qua nút chọn"
    )
    await update.message.reply_text(msg)

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    limit = int(context.args[0]) if context.args else 10
    rows = list_transactions(user.id, limit)
    if not rows:
        return await update.message.reply_text("📭 Chưa có giao dịch nào.")
    lines = []
    for r in rows:
        _id, amount, ttype, cat, note, created = r
        created_local = created.replace("T", " ")[:19]
        lines.append(f"{_id}. [{ttype}] {amount:,.0f} — {cat} {('- ' + note) if note else ''}\n    {created_local}")
    await update.message.reply_text("\n\n".join(lines))

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    now = datetime.utcnow()
    if context.args:
        try:
            y, m = map(int, context.args[0].split("-"))
        except:
            return await update.message.reply_text("Sai định dạng. Dùng /report YYYY-MM")
    else:
        y, m = now.year, now.month

    totals, by_cat = report_month(user.id, y, m)
    income = totals.get("income", 0)
    expense = totals.get("expense", 0)
    balance = income - expense

    msg = f"📊 Báo cáo {y}-{m:02d}\nTổng thu: {income:,.0f}\nTổng chi: {expense:,.0f}\nSố dư: {balance:,.0f}\n\nChi tiết:\n"
    if not by_cat:
        msg += "(Không có giao dịch)"
    else:
        for ttype, cat, s in by_cat:
            msg += f"- [{ttype}] {cat or 'Khác'}: {s:,.0f}\n"
    await update.message.reply_text(msg)

# Quick menu (tùy chọn)
COMMON_CATS = ["food", "transport", "salary", "shopping", "other"]

async def quick_add_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(f"Chi {c}", callback_data=f"quick_expense|{c}")]
        for c in COMMON_CATS
    ]
    keyboard.append([InlineKeyboardButton("Thu lương", callback_data="quick_income|salary")])
    await update.message.reply_text("Chọn nhanh:", reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split("|")
    if len(parts) != 2:
        return await query.edit_message_text("Lỗi dữ liệu.")
    action, cat = parts
    user = update.effective_user
    if action == "quick_expense":
        add_transaction(user.id, 0, "expense", cat, "quick add (0)")
        await query.edit_message_text(f"Đã thêm chi {cat} (0đ)")
    elif action == "quick_income":
        add_transaction(user.id, 0, "income", cat, "quick add (0)")
        await query.edit_message_text(f"Đã thêm thu {cat} (0đ)")

# === Xử lý tin nhắn tự nhiên ===
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    parsed = parse_free_text(text)
    if not parsed:
        return await update.message.reply_text(
            "❓ Không hiểu. Thử gõ như: 'vietin chi 10k ăn sáng' hoặc 'momo thu 200k khách chuyển'."
        )
    data = parsed
    add_transaction(user.id, data["amount"], data["type"], data["account"], data["note"])
    await update.message.reply_text(
        f"✅ Đã ghi {data['type']} {data['amount']:.0f}đ từ {data['account']} - {data['note']}"
    )

# === Chạy bot ===
def main():
    if not TOKEN or TOKEN.startswith("THAY_TOKEN"):
        print("⚠️ Hãy điền token thật của bạn vào biến TOKEN trong file.")
        return

    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("quick", quick_add_menu))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    print("🤖 Bot đang chạy... (Ctrl+C để dừng)")
    app.run_polling()

if __name__ == "__main__":
    main()
