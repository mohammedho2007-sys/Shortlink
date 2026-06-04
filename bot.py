#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Rewards Bot – One‑file version
Arabic interface, referral system, task links, Supabase REST API, admin panel.
Deployable on Railway.
"""

import os
import re
import asyncio
import logging
from typing import Optional, List, Dict, Any

import requests
from dotenv import load_dotenv
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, CallbackQueryHandler, filters
)
from aiohttp import web

# ------------------------------
#  Environment & Configuration
# ------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("8837715030:AAHffgOJi12MgvlmMxf_wKHtE7SSM8QUBF8")
SUPABASE_URL = os.getenv("https://xlvcpsjntuymdtsmernk.supabase.co")
SUPABASE_KEY = os.getenv("sb_publishable_HqkHcY64gIGbwuBO9urINg_vok-GaFZ")
ADMIN_ID = int(os.getenv("8411026975"))
DOMAIN = os.getenv("DOMAIN")          # e.g. https://your-app.railway.app
PORT = int(os.environ.get("PORT", 8080))

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Supabase REST headers
SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

# ------------------------------
#  Arabic UI Texts
# ------------------------------
MAIN_MENU_TEXTS = {
    "tasks": "📋 عرض المهام",
    "balance": "💰 رصيدي",
    "referral": "👥 الإحالات",
    "withdraw": "💸 سحب النقود"
}
ADMIN_BUTTON_TEXT = "👑 لوحة الإدارة"

# ------------------------------
#  Supabase REST Helpers (async)
# ------------------------------
def _rest_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Synchronous REST call to Supabase."""
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    resp = requests.request(method, url, headers=SUPABASE_HEADERS, json=data)
    resp.raise_for_status()
    return resp.json() if resp.text else {}

async def supabase_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Async wrapper for Supabase REST."""
    return await asyncio.to_thread(_rest_request, method, endpoint, data)

# ---------- Users ----------
async def get_user(telegram_id: int) -> Optional[dict]:
    res = await supabase_request("GET", f"users?telegram_id=eq.{telegram_id}&select=*")
    return res[0] if res else None

async def create_user(telegram_id: int, username: str = None, referred_by: int = None) -> dict:
    data = {
        "telegram_id": telegram_id,
        "username": username,
        "points": 0,
        "referred_by": referred_by
    }
    await supabase_request("POST", "users", data)
    return await get_user(telegram_id)

async def update_user_points(telegram_id: int, delta: int) -> None:
    user = await get_user(telegram_id)
    if user:
        new_points = user["points"] + delta
        await supabase_request("PATCH", f"users?telegram_id=eq.{telegram_id}", {"points": new_points})

async def get_user_stats(telegram_id: int) -> dict:
    user = await get_user(telegram_id)
    if not user:
        return {"points": 0, "completed_tasks": 0, "referrals": 0}
    comp = await supabase_request("GET", f"completed_tasks?user_id=eq.{user['id']}&select=id")
    refs = await supabase_request("GET", f"users?referred_by=eq.{telegram_id}&select=id")
    return {
        "points": user["points"],
        "completed_tasks": len(comp),
        "referrals": len(refs)
    }

async def add_referrer_points(referrer_telegram_id: int) -> None:
    await update_user_points(referrer_telegram_id, 50)

# ---------- Tasks ----------
async def get_active_tasks() -> List[dict]:
    return await supabase_request("GET", "tasks?active=eq.true&select=*")

async def get_task(task_id: int) -> Optional[dict]:
    res = await supabase_request("GET", f"tasks?id=eq.{task_id}&select=*")
    return res[0] if res else None

async def add_task(title: str, reward: int, target_url: str) -> None:
    data = {"title": title, "reward": reward, "target_url": target_url, "active": True}
    await supabase_request("POST", "tasks", data)

async def delete_task(task_id: int) -> None:
    await supabase_request("DELETE", f"tasks?id=eq.{task_id}")

# ---------- Completed Tasks ----------
async def is_task_completed(user_telegram_id: int, task_id: int) -> bool:
    user = await get_user(user_telegram_id)
    if not user:
        return False
    res = await supabase_request("GET", f"completed_tasks?user_id=eq.{user['id']}&task_id=eq.{task_id}")
    return len(res) > 0

async def complete_task(user_telegram_id: int, task_id: int) -> bool:
    """Award points and record completion. Returns True if succeeded."""
    if await is_task_completed(user_telegram_id, task_id):
        return False
    user = await get_user(user_telegram_id)
    task = await get_task(task_id)
    if not user or not task or not task["active"]:
        return False
    # record completion
    await supabase_request("POST", "completed_tasks", {"user_id": user["id"], "task_id": task_id})
    # award points
    await update_user_points(user_telegram_id, task["reward"])
    return True

# ---------- Withdrawals ----------
async def create_withdrawal_request(telegram_id: int, amount: int) -> bool:
    user = await get_user(telegram_id)
    if not user or user["points"] < amount or amount < 1000:
        return False
    pending = await supabase_request("GET", f"withdrawals?user_id=eq.{user['id']}&status=eq.pending")
    if pending:
        return False
    data = {"user_id": user["id"], "amount": amount, "status": "pending"}
    await supabase_request("POST", "withdrawals", data)
    return True

async def get_pending_withdrawals() -> List[dict]:
    # Join with users table to get telegram_id and username
    return await supabase_request(
        "GET",
        "withdrawals?status=eq.pending&select=*,users(telegram_id,username)"
    )

async def get_user_by_id(user_id: int) -> Optional[dict]:
    res = await supabase_request("GET", f"users?id=eq.{user_id}&select=*")
    return res[0] if res else None

async def update_withdrawal_status(withdrawal_id: int, status: str, deduct_points: bool = False) -> None:
    """status: approved / rejected. If deduct_points=True, subtract from user balance."""
    w_req_list = await supabase_request("GET", f"withdrawals?id=eq.{withdrawal_id}&select=*")
    if not w_req_list:
        return
    w = w_req_list[0]
    if deduct_points and status == "approved":
        user = await get_user_by_id(w["user_id"])
        if user and user["points"] >= w["amount"]:
            await update_user_points(user["telegram_id"], -w["amount"])
    await supabase_request("PATCH", f"withdrawals?id=eq.{withdrawal_id}", {"status": status})

# ---------- Admin Statistics ----------
async def get_statistics() -> dict:
    users = await supabase_request("GET", "users?select=id")
    completed = await supabase_request("GET", "completed_tasks?select=id")
    comp_with_tasks = await supabase_request(
        "GET", "completed_tasks?select=task_id(*.reward)"
    )
    total_points = sum(
        item["task_id"]["reward"] for item in comp_with_tasks if item.get("task_id")
    )
    pending = await supabase_request("GET", "withdrawals?status=eq.pending&select=id")
    return {
        "total_users": len(users),
        "total_completed_tasks": len(completed),
        "total_points_distributed": total_points,
        "pending_withdrawals": len(pending)
    }

async def get_all_users() -> List[dict]:
    return await supabase_request("GET", "users?select=telegram_id")

# ------------------------------
#  Telegram Bot – User Handlers
# ------------------------------
async def main_menu_keyboard(is_admin: bool = False):
    keys = [
        [KeyboardButton(MAIN_MENU_TEXTS["tasks"])],
        [KeyboardButton(MAIN_MENU_TEXTS["balance"]), KeyboardButton(MAIN_MENU_TEXTS["referral"])],
        [KeyboardButton(MAIN_MENU_TEXTS["withdraw"])]
    ]
    if is_admin:
        keys.append([KeyboardButton(ADMIN_BUTTON_TEXT)])
    return ReplyKeyboardMarkup(keys, resize_keyboard=True)

async def start(update: Update, context):
    user = update.effective_user
    telegram_id = user.id
    referred_by = None

    # Parse referrer from start parameter
    if context.args and len(context.args) > 0:
        match = re.match(r"ref_(\d+)", context.args[0])
        if match:
            ref_id = int(match.group(1))
            if ref_id != telegram_id:
                referred_by = ref_id
                await add_referrer_points(ref_id)

    existing = await get_user(telegram_id)
    if not existing:
        await create_user(telegram_id, user.username, referred_by)
        msg = "✨ مرحباً! تم تسجيلك بنجاح.\nاستخدم الأزرار أدناه للبدء."
    else:
        msg = "👋 أهلاً بك مجدداً!"

    is_admin = (telegram_id == ADMIN_ID)
    await update.message.reply_text(msg, reply_markup=await main_menu_keyboard(is_admin))

async def show_tasks(update: Update, context):
    user_id = update.effective_user.id
    tasks = await get_active_tasks()
    if not tasks:
        await update.message.reply_text("⚠️ لا توجد مهام حالياً.")
        return
    for task in tasks:
        link = f"{DOMAIN}/go?task_id={task['id']}&user_id={user_id}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 تنفيذ المهمة", url=link)]])
        text = f"📌 *{task['title']}*\n💰 المكافأة: {task['reward']} نقطة\n🔗 اضغط الزر لزيارة الموقع."
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def show_balance(update: Update, context):
    user_id = update.effective_user.id
    stats = await get_user_stats(user_id)
    text = f"💎 *رصيدك:* {stats['points']} نقطة\n✅ *المهام المنجزة:* {stats['completed_tasks']}\n👥 *عدد المدعوين:* {stats['referrals']}"
    await update.message.reply_text(text, parse_mode="Markdown")

async def show_referral(update: Update, context):
    bot_username = (await context.bot.get_me()).username
    user_id = update.effective_user.id
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    stats = await get_user_stats(user_id)
    text = f"👥 *نظام الإحالات*\n💰 لكل صديق جديد يدعوه، تحصل على 50 نقطة.\n\nرابط الإحالة الخاص بك:\n`{link}`\n\n👥 عدد المدعوين: {stats['referrals']}"
    await update.message.reply_text(text, parse_mode="Markdown")

# Withdrawal conversation states
AWAITING_WITHDRAW_AMOUNT = 1

async def request_withdrawal(update: Update, context):
    user_id = update.effective_user.id
    stats = await get_user_stats(user_id)
    if stats["points"] < 1000:
        await update.message.reply_text("⚠️ الحد الأدنى للسحب هو 1000 نقطة. رصيدك غير كافٍ.")
        return
    context.user_data["awaiting_withdraw"] = True
    await update.message.reply_text("💸 أدخل المبلغ الذي تريد سحبه (أرقام فقط):")
    return AWAITING_WITHDRAW_AMOUNT

async def process_withdraw_amount(update: Update, context):
    if not context.user_data.get("awaiting_withdraw"):
        return
    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال رقم صحيح.")
        return AWAITING_WITHDRAW_AMOUNT
    user_id = update.effective_user.id
    success = await create_withdrawal_request(user_id, amount)
    if success:
        await update.message.reply_text("✅ تم إرسال طلب السحب إلى المشرف. سيتم إعلامك عند الموافقة.")
    else:
        await update.message.reply_text("❌ لا يمكن تقديم الطلب. تحقق من رصيدك أو من وجود طلب سابق معلق.")
    context.user_data.pop("awaiting_withdraw", None)
    return ConversationHandler.END

async def cancel_withdraw(update: Update, context):
    context.user_data.pop("awaiting_withdraw", None)
    await update.message.reply_text("تم إلغاء طلب السحب.")
    return ConversationHandler.END

# ------------------------------
#  Admin Handlers
# ------------------------------
def is_admin_user(update: Update) -> bool:
    return update.effective_user.id == ADMIN_ID

async def admin_menu(update: Update, context):
    if not is_admin_user(update):
        return
    keyboard = [
        ["➕ إضافة مهمة", "❌ حذف مهمة"],
        ["📊 الإحصائيات", "📢 إرسال رسالة جماعية"],
        ["💵 طلبات السحب", "🔙 رجوع"]
    ]
    await update.message.reply_text(
        "🔧 *لوحة التحكم*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )

# Add task conversation states
ASK_TITLE, ASK_REWARD, ASK_URL = range(10, 13)

async def add_task_start(update: Update, context):
    if not is_admin_user(update):
        return
    await update.message.reply_text("أرسل عنوان المهمة:")
    return ASK_TITLE

async def add_task_title(update: Update, context):
    context.user_data["task_title"] = update.message.text
    await update.message.reply_text("أرسل مكافأة المهمة (عدد النقاط):")
    return ASK_REWARD

async def add_task_reward(update: Update, context):
    try:
        reward = int(update.message.text)
        context.user_data["task_reward"] = reward
        await update.message.reply_text("أرسل رابط الهدف (target URL):")
        return ASK_URL
    except ValueError:
        await update.message.reply_text("❌ يجب أن يكون المكافأة رقماً. أعد المحاولة:")
        return ASK_REWARD

async def add_task_url(update: Update, context):
    url = update.message.text
    title = context.user_data["task_title"]
    reward = context.user_data["task_reward"]
    await add_task(title, reward, url)
    await update.message.reply_text(f"✅ تمت إضافة المهمة: {title}")
    return ConversationHandler.END

async def list_tasks_for_delete(update: Update, context):
    if not is_admin_user(update):
        return
    tasks = await get_active_tasks()
    if not tasks:
        await update.message.reply_text("لا توجد مهام لحذفها.")
        return
    keyboard = [[InlineKeyboardButton(t["title"], callback_data=f"del_{t['id']}")] for t in tasks]
    await update.message.reply_text("اختر مهمة لحذفها:", reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_task_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[1])
    await delete_task(task_id)
    await query.edit_message_text("✅ تم حذف المهمة.")

async def show_statistics(update: Update, context):
    if not is_admin_user(update):
        return
    stats = await get_statistics()
    text = (
        f"📊 *الإحصائيات*\n"
        f"👥 المستخدمين: {stats['total_users']}\n"
        f"✅ المهام المنجزة: {stats['total_completed_tasks']}\n"
        f"💎 إجمالي النقاط الموزعة: {stats['total_points_distributed']}\n"
        f"⏳ طلبات السحب المعلقة: {stats['pending_withdrawals']}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# Broadcast conversation
AWAITING_BROADCAST_MSG = 20

async def broadcast_prompt(update: Update, context):
    if not is_admin_user(update):
        return
    await update.message.reply_text("أرسل الرسالة التي تريد إذاعتها لجميع المستخدمين:")
    return AWAITING_BROADCAST_MSG

async def send_broadcast(update: Update, context):
    if not context.user_data.get("broadcasting"):
        return
    msg = update.message.text
    users = await get_all_users()
    sent = 0
    for u in users:
        try:
            await context.bot.send_message(
                chat_id=u["telegram_id"],
                text=f"📢 *رسالة جماعية*\n{msg}",
                parse_mode="Markdown"
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Broadcast failed to {u['telegram_id']}: {e}")
    await update.message.reply_text(f"✅ تم إرسال الرسالة إلى {sent} مستخدم.")
    context.user_data.pop("broadcasting", None)
    return ConversationHandler.END

async def cancel_broadcast(update: Update, context):
    context.user_data.pop("broadcasting", None)
    await update.message.reply_text("تم إلغاء الإذاعة.")
    return ConversationHandler.END

async def withdrawal_requests_list(update: Update, context):
    if not is_admin_user(update):
        return
    pending = await get_pending_withdrawals()
    if not pending:
        await update.message.reply_text("لا توجد طلبات سحب معلقة.")
        return
    for req in pending:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{req['id']}"),
             InlineKeyboardButton("❌ رفض", callback_data=f"reject_{req['id']}")]
        ])
        user = req.get("users", {})
        text = f"👤 المستخدم: {user.get('username', user.get('telegram_id'))}\n💵 المبلغ: {req['amount']} نقطة"
        await update.message.reply_text(text, reply_markup=keyboard)

async def handle_withdrawal_action(update: Update, context):
    query = update.callback_query
    await query.answer()
    action, w_id = query.data.split("_")
    w_id = int(w_id)
    if action == "approve":
        await update_withdrawal_status(w_id, "approved", deduct_points=True)
        await query.edit_message_text("✅ تمت الموافقة على السحب.")
    elif action == "reject":
        await update_withdrawal_status(w_id, "rejected")
        await query.edit_message_text("❌ تم رفض السحب.")

async def back_to_main(update: Update, context):
    user_id = update.effective_user.id
    is_admin = (user_id == ADMIN_ID)
    await update.message.reply_text(
        "🔙 رجوع إلى القائمة الرئيسية",
        reply_markup=await main_menu_keyboard(is_admin)
    )
    return ConversationHandler.END

# ------------------------------
#  Web Server – /go Endpoint
# ------------------------------
async def handle_go(request: web.Request) -> web.Response:
    """Awards points when user visits the task link."""
    params = request.rel_url.query
    task_id_str = params.get("task_id")
    user_id_str = params.get("user_id")
    if not task_id_str or not user_id_str:
        return web.Response(text="طلب غير صحيح", status=400)

    try:
        task_id = int(task_id_str)
        user_telegram_id = int(user_id_str)
    except ValueError:
        return web.Response(text="معطيات غير صالحة", status=400)

    # Check and award points
    success = await complete_task(user_telegram_id, task_id)
    if success:
        # Optionally send a real-time Telegram notification
        try:
            bot_app = request.app["bot_app"]
            await bot_app.bot.send_message(
                chat_id=user_telegram_id,
                text="🎉 *تهانينا!*\nتم إكمال المهمة بنجاح وأضيفت النقاط إلى رصيدك.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Notification failed: {e}")

        html_response = """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><title>تم بنجاح</title></head>
        <body style="text-align:center;font-family:Arial;padding:50px;">
            <h1>✅ تم إكمال المهمة!</h1>
            <p>تم إضافة النقاط إلى حسابك في البوت.</p>
            <p>يمكنك العودة إلى Telegram ومتابعة المهام.</p>
        </body>
        </html>
        """
        return web.Response(text=html_response, content_type="text/html")
    else:
        html_response = """
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"><title>خطأ</title></head>
        <body style="text-align:center;font-family:Arial;padding:50px;">
            <h1>⚠️ لا يمكن إكمال المهمة</h1>
            <p>إما أنك أكملت هذه المهمة مسبقاً أو أن المهمة غير موجودة.</p>
        </body>
        </html>
        """
        return web.Response(text=html_response, content_type="text/html", status=400)

async def web_server(app: web.Application):
    """Start aiohttp server."""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server running on port {PORT}")

# ------------------------------
#  Main Application
# ------------------------------
def main():
    # Create Telegram application
    application = Application.builder().token(BOT_TOKEN).build()
    application.bot_data["ADMIN_ID"] = ADMIN_ID
    application.bot_data["DOMAIN"] = DOMAIN

    # --- User handlers ---
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex(f"^{MAIN_MENU_TEXTS['tasks']}$"), show_tasks))
    application.add_handler(MessageHandler(filters.Regex(f"^{MAIN_MENU_TEXTS['balance']}$"), show_balance))
    application.add_handler(MessageHandler(filters.Regex(f"^{MAIN_MENU_TEXTS['referral']}$"), show_referral))

    withdraw_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{MAIN_MENU_TEXTS['withdraw']}$"), request_withdrawal)],
        states={
            AWAITING_WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_withdraw_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel_withdraw)],
    )
    application.add_handler(withdraw_conv)

    # --- Admin handlers ---
    application.add_handler(MessageHandler(filters.Regex(f"^{ADMIN_BUTTON_TEXT}$"), admin_menu))
    application.add_handler(MessageHandler(filters.Regex("^🔙 رجوع$"), back_to_main))

    # Add task conversation
    add_task_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ إضافة مهمة$"), add_task_start)],
        states={
            ASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_title)],
            ASK_REWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_reward)],
            ASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_url)],
        },
        fallbacks=[CommandHandler("cancel", back_to_main)],
    )
    application.add_handler(add_task_conv)

    application.add_handler(MessageHandler(filters.Regex("^❌ حذف مهمة$"), list_tasks_for_delete))
    application.add_handler(CallbackQueryHandler(delete_task_callback, pattern="^del_"))
    application.add_handler(MessageHandler(filters.Regex("^📊 الإحصائيات$"), show_statistics))

    # Broadcast conversation
    broadcast_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📢 إرسال رسالة جماعية$"), broadcast_prompt)],
        states={
            AWAITING_BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, send_broadcast)],
        },
        fallbacks=[CommandHandler("cancel", cancel_broadcast)],
    )
    application.add_handler(broadcast_conv)

    application.add_handler(MessageHandler(filters.Regex("^💵 طلبات السحب$"), withdrawal_requests_list))
    application.add_handler(CallbackQueryHandler(handle_withdrawal_action, pattern="^(approve|reject)_"))

    # --- Web server setup ---
    web_app = web.Application()
    web_app.router.add_get("/go", handle_go)
    web_app["bot_app"] = application   # store for notification use

    async def on_startup():
        # Start web server in background
        asyncio.create_task(web_server(web_app))
        logger.info("Web server task started")

    application.post_init = on_startup

    # Start bot polling
    logger.info("Bot started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
