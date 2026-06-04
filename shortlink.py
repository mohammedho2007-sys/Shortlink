#!/usr/bin/env python3
# Telegram Rewards Bot - Production Ready
# Compatible with Pydroid3 and python-telegram-bot v22
# Uses Supabase as database (async)
# Language: Arabic

import os
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ConversationHandler, ContextTypes
)
from supabase import create_async_client
from supabase.lib.client_options import ClientOptions

# ========== CONFIGURATION ==========
# Environment variables (set before running)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DOMAIN = os.environ.get("DOMAIN", "https://YOUR_DOMAIN.up.railway.app")
# Constants
MIN_WITHDRAW = 1000
REFERRAL_REWARD = 50
TASK_REWARD = 5

# Conversation states
ADD_TASK_TITLE, ADD_TASK_URL = range(2)
DELETE_TASK_SELECT = 10
BROADCAST_MESSAGE = 20

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== SUPABASE INITIALIZATION ==========
supabase = None

async def init_supabase():
    global supabase
    supabase = await create_async_client(
        supabase_url=SUPABASE_URL,
        supabase_key=SUPABASE_KEY,
        options=ClientOptions(postgrest_client_timeout=30)
    )
    logger.info("Supabase client initialized")

# ========== DATABASE HELPERS ==========
async def get_user(user_id: int) -> Optional[Dict]:
    """Fetch user by Telegram user_id"""
    try:
        res = await supabase.table("users").select("*").eq("user_id", user_id).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_user error: {e}")
        return None

async def register_user(user_id: int, username: str, referred_by: Optional[int] = None) -> bool:
    """Register new user, return success. Also handle referral reward."""
    try:
        # Check if already exists
        existing = await get_user(user_id)
        if existing:
            # Update username in case changed
            await supabase.table("users").update({"username": username}).eq("user_id", user_id).execute()
            return True
        
        # Insert new user
        user_data = {
            "user_id": user_id,
            "username": username,
            "points": 0,
            "referred_by": referred_by,
            "created_at": datetime.utcnow().isoformat()
        }
        await supabase.table("users").insert(user_data).execute()
        
        # Give referral reward if referred by someone
        if referred_by and referred_by != user_id:
            referrer = await get_user(referred_by)
            if referrer:
                new_points = referrer["points"] + REFERRAL_REWARD
                await supabase.table("users").update({"points": new_points}).eq("user_id", referred_by).execute()
                logger.info(f"Referral reward {REFERRAL_REWARD} given to {referred_by} for inviting {user_id}")
        return True
    except Exception as e:
        logger.error(f"register_user error: {e}")
        return False

async def add_points(user_id: int, points: int) -> bool:
    """Add points to user"""
    try:
        user = await get_user(user_id)
        if not user:
            return False
        new_points = user["points"] + points
        await supabase.table("users").update({"points": new_points}).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        logger.error(f"add_points error: {e}")
        return False

async def get_tasks() -> List[Dict]:
    """Get all tasks"""
    try:
        res = await supabase.table("tasks").select("*").order("id").execute()
        return res.data
    except Exception as e:
        logger.error(f"get_tasks error: {e}")
        return []

async def add_task(title: str, url: str = "", reward: int = TASK_REWARD) -> bool:
    """Add new task"""
    try:
        await supabase.table("tasks").insert({
            "title": title,
            "url": url,
            "reward": reward
        }).execute()
        return True
    except Exception as e:
        logger.error(f"add_task error: {e}")
        return False

async def delete_task(task_id: int) -> bool:
    """Delete task by id"""
    try:
        await supabase.table("tasks").delete().eq("id", task_id).execute()
        return True
    except Exception as e:
        logger.error(f"delete_task error: {e}")
        return False

async def is_task_completed(user_id: int, task_id: int) -> bool:
    """Check if user already completed this task"""
    try:
        res = await supabase.table("completed_tasks").select("*").eq("user_id", user_id).eq("task_id", task_id).execute()
        return len(res.data) > 0
    except Exception as e:
        logger.error(f"is_task_completed error: {e}")
        return False

async def complete_task(user_id: int, task_id: int) -> bool:
    """Mark task as completed and give reward if not already completed"""
    try:
        if await is_task_completed(user_id, task_id):
            return False
        
        # Get task reward
        task_res = await supabase.table("tasks").select("reward").eq("id", task_id).execute()
        if not task_res.data:
            return False
        reward = task_res.data[0]["reward"]
        
        # Insert into completed_tasks
        await supabase.table("completed_tasks").insert({
            "user_id": user_id,
            "task_id": task_id,
            "completed_at": datetime.utcnow().isoformat()
        }).execute()
        
        # Add points to user
        await add_points(user_id, reward)
        return True
    except Exception as e:
        logger.error(f"complete_task error: {e}")
        return False

async def create_withdraw_request(user_id: int, amount: int) -> bool:
    """Create withdrawal request"""
    try:
        user = await get_user(user_id)
        if not user or user["points"] < amount or amount < MIN_WITHDRAW:
            return False
        
        await supabase.table("withdraw_requests").insert({
            "user_id": user_id,
            "amount": amount,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        return True
    except Exception as e:
        logger.error(f"create_withdraw_request error: {e}")
        return False

async def get_withdraw_requests(status: str = "pending") -> List[Dict]:
    """Get withdrawal requests by status"""
    try:
        res = await supabase.table("withdraw_requests").select("*").eq("status", status).order("created_at").execute()
        return res.data
    except Exception as e:
        logger.error(f"get_withdraw_requests error: {e}")
        return []

async def update_withdraw_request(request_id: int, status: str, admin_approve: bool = True) -> bool:
    """Update withdrawal request status, deduct points if approved"""
    try:
        req_res = await supabase.table("withdraw_requests").select("*").eq("id", request_id).execute()
        if not req_res.data:
            return False
        req = req_res.data[0]
        
        if status == "approved" and admin_approve:
            # Deduct points from user
            user = await get_user(req["user_id"])
            if user and user["points"] >= req["amount"]:
                new_points = user["points"] - req["amount"]
                await supabase.table("users").update({"points": new_points}).eq("user_id", req["user_id"]).execute()
            else:
                return False
        
        await supabase.table("withdraw_requests").update({"status": status}).eq("id", request_id).execute()
        return True
    except Exception as e:
        logger.error(f"update_withdraw_request error: {e}")
        return False

async def get_statistics() -> Dict:
    """Get bot statistics"""
    try:
        users_res = await supabase.table("users").select("count", count="exact").execute()
        total_users = users_res.count
        
        points_res = await supabase.table("users").select("points").execute()
        total_points = sum(u["points"] for u in points_res.data)
        
        completed_res = await supabase.table("completed_tasks").select("count", count="exact").execute()
        total_completed = completed_res.count
        
        pending_withdraw = len(await get_withdraw_requests("pending"))
        
        return {
            "total_users": total_users,
            "total_points": total_points,
            "total_completed_tasks": total_completed,
            "pending_withdrawals": pending_withdraw
        }
    except Exception as e:
        logger.error(f"get_statistics error: {e}")
        return {
            "total_users": 0,
            "total_points": 0,
            "total_completed_tasks": 0,
            "pending_withdrawals": 0
        }

async def get_referral_count(user_id: int) -> int:
    """Count how many users this user referred"""
    try:
        res = await supabase.table("users").select("count", count="exact").eq("referred_by", user_id).execute()
        return res.count or 0
    except Exception as e:
        logger.error(f"get_referral_count error: {e}")
        return 0

async def broadcast_message(message: str, bot) -> int:
    """Send message to all users, return success count"""
    try:
        users_res = await supabase.table("users").select("user_id").execute()
        success = 0
        for user in users_res.data:
            try:
                await bot.send_message(chat_id=user["user_id"], text=message)
                success += 1
                await asyncio.sleep(0.05)  # Avoid flood limits
            except Exception as e:
                logger.warning(f"Broadcast failed to {user['user_id']}: {e}")
        return success
    except Exception as e:
        logger.error(f"broadcast_message error: {e}")
        return 0

# ========== KEYBOARDS ==========
def get_main_keyboard(is_admin: bool = False):
    """Main menu keyboard"""
    buttons = [
        [KeyboardButton("📋 المهام"), KeyboardButton("💰 رصيدي")],
        [KeyboardButton("👥 دعوة الأصدقاء"), KeyboardButton("💸 سحب الأرباح")]
    ]
    if is_admin:
        buttons.append([KeyboardButton("⚙️ لوحة التحكم")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_admin_keyboard():
    """Admin menu keyboard"""
    buttons = [
        [KeyboardButton("➕ إضافة مهمة"), KeyboardButton("❌ حذف مهمة")],
        [KeyboardButton("📊 الإحصائيات"), KeyboardButton("📢 إذاعة")],
        [KeyboardButton("💵 طلبات السحب"), KeyboardButton("🔙 العودة للرئيسية")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def is_admin(user_id: int) -> bool:
    """Check if user is admin"""
    return user_id == ADMIN_ID

# ========== HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - register user, handle referrals"""
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name
    
    # Check for referral in deep link
    referred_by = None
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referred_by = int(arg.split("_")[1])
            except:
                pass
    
    # Register user
    await register_user(user_id, username, referred_by)
    
    # Welcome message
    welcome_text = (
        f"✨ مرحباً {user.first_name}! ✨\n\n"
        "بوت المكافآت يساعدك في ربح النقاط عبر إتمام المهام.\n"
        "استخدم الأزرار أدناه للبدء.\n\n"
        f"💰 رصيدك الحالي: 0 نقطة"
    )
    
    keyboard = get_main_keyboard(is_admin(user_id))
    await update.message.reply_text(welcome_text, reply_markup=keyboard)

async def main_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle main menu buttons"""
    text = update.message.text
    user_id = update.effective_user.id
    
    if text == "📋 المهام":
        await show_tasks(update, context)
    elif text == "💰 رصيدي":
        await show_balance(update, context)
    elif text == "👥 دعوة الأصدقاء":
        await show_referral(update, context)
    elif text == "💸 سحب الأرباح":
        await withdraw_request(update, context)
    elif text == "⚙️ لوحة التحكم" and is_admin(user_id):
        await admin_panel(update, context)
    elif text == "🔙 العودة للرئيسية":
        keyboard = get_main_keyboard(is_admin(user_id))
        await update.message.reply_text("🔹 القائمة الرئيسية 🔹", reply_markup=keyboard)
    else:
        await update.message.reply_text("❓ استخدم الأزرار من القائمة فقط.")

async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display list of available tasks"""
    tasks = await get_tasks()
    if not tasks:
        await update.message.reply_text("📭 لا توجد مهام متاحة حالياً. ترقب المزيد قريباً!")
        return
    
    keyboard = []
    for task in tasks:
        keyboard.append([InlineKeyboardButton(f"📌 {task['title']}", callback_data=f"task_{task['id']}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📋 قائمة المهام المتاحة:\nاختر مهمة لمعرفة التفاصيل:", reply_markup=reply_markup)

async def task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show task details and completion button"""
    query = update.callback_query
    await query.answer()
    
    task_id = int(query.data.split("_")[1])
    user_id = query.from_user.id
    
    # Get task details
    tasks = await get_tasks()
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        await query.edit_message_text("⚠️ المهمة غير موجودة!")
        return
    
    # Check if already completed
    completed = await is_task_completed(user_id, task_id)
    reward_link = f"{DOMAIN}/reward?user_id={user_id}&task_id={task_id}"
    
    message = f"📌 **{task['title']}**\n\n"
    message += f"💰 المكافأة: {task['reward']} نقطة\n"
    if task.get('url'):
        message += f"🔗 رابط المهمة: {task['url']}\n\n"
    message += f"📎 رابط الإكمال التلقائي:\n`{reward_link}`\n\n"
    if completed:
        message += "✅ **لقد أكملت هذه المهمة مسبقاً!**"
    else:
        message += "⬅️ بعد تنفيذ المهمة، اضغط زر 'تم الإكمال' للحصول على المكافأة."
    
    keyboard = []
    if not completed:
        keyboard.append([InlineKeyboardButton("✅ تم الإكمال", callback_data=f"complete_{task_id}")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_tasks")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")

async def complete_task_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle task completion confirmation"""
    query = update.callback_query
    await query.answer()
    
    task_id = int(query.data.split("_")[1])
    user_id = query.from_user.id
    
    success = await complete_task(user_id, task_id)
    
    if success:
        # Get task reward
        tasks = await get_tasks()
        task = next((t for t in tasks if t["id"] == task_id), None)
        reward = task["reward"] if task else TASK_REWARD
        
        await query.edit_message_text(
            f"✅ **تم إكمال المهمة بنجاح!**\n\n"
            f"🎁 لقد حصلت على {reward} نقطة.\n"
            f"💰 استمر في إتمام المزيد من المهام لزيادة رصيدك.",
            parse_mode="Markdown"
        )
        # Notify user in chat? Already edited
    else:
        await query.edit_message_text(
            "⚠️ **لا يمكن إكمال المهمة**\n"
            "إما أنك أكملتها مسبقاً أو حدث خطأ.",
            parse_mode="Markdown"
        )

async def back_to_tasks_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to tasks list"""
    query = update.callback_query
    await query.answer()
    tasks = await get_tasks()
    if not tasks:
        await query.edit_message_text("📭 لا توجد مهام متاحة.")
        return
    
    keyboard = []
    for task in tasks:
        keyboard.append([InlineKeyboardButton(f"📌 {task['title']}", callback_data=f"task_{task['id']}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("📋 قائمة المهام:", reply_markup=reply_markup)

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display user balance"""
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if user:
        balance = user["points"]
        await update.message.reply_text(
            f"💰 **رصيدك الحالي:** {balance} نقطة\n\n"
            f"💡 يمكنك سحب الأرباح عند وصول الرصيد إلى {MIN_WITHDRAW} نقطة.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("⚠️ حدث خطأ في جلب الرصيد.")

async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show referral info and invite link"""
    user_id = update.effective_user.id
    refer_count = await get_referral_count(user_id)
    bot_username = context.bot.username
    
    invite_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    message = (
        f"👥 **نظام الإحالات**\n\n"
        f"🎁 لكل صديق تدعوه، تحصل على {REFERRAL_REWARD} نقطة.\n"
        f"👤 عدد من دعوتهم: {refer_count}\n\n"
        f"🔗 رابط الدعوة الخاص بك:\n`{invite_link}`\n\n"
        f"📤 شارك الرابط مع أصدقائك لربح النقاط!"
    )
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def withdraw_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request withdrawal"""
    user_id = update.effective_user.id
    user = await get_user(user_id)
    
    if not user:
        await update.message.reply_text("⚠️ يرجى استخدام /start أولاً.")
        return
    
    balance = user["points"]
    if balance < MIN_WITHDRAW:
        await update.message.reply_text(
            f"❌ رصيدك لا يكفي للسحب.\n"
            f"💰 رصيدك: {balance} نقطة\n"
            f"🎯 الحد الأدنى للسحب: {MIN_WITHDRAW} نقطة"
        )
        return
    
    # Create withdrawal request
    success = await create_withdraw_request(user_id, balance)
    if success:
        await update.message.reply_text(
            f"✅ تم تقديم طلب سحب بمبلغ {balance} نقطة.\n"
            f"⏳ سيتم مراجعة الطلب من قبل الإدارة وإرسال الأرباح إلى حسابك."
        )
    else:
        await update.message.reply_text("❌ حدث خطأ في تقديم الطلب. حاول مرة أخرى.")

# ========== ADMIN HANDLERS ==========
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 غير مصرح لك بالدخول إلى لوحة التحكم.")
        return
    
    keyboard = get_admin_keyboard()
    await update.message.reply_text("⚙️ **لوحة تحكم المشرف**", parse_mode="Markdown", reply_markup=keyboard)

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show statistics for admin"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    
    stats = await get_statistics()
    message = (
        f"📊 **إحصائيات البوت**\n\n"
        f"👥 إجمالي المستخدمين: {stats['total_users']}\n"
        f"💰 إجمالي النقاط الموزعة: {stats['total_points']}\n"
        f"✅ المهام المكتملة: {stats['total_completed_tasks']}\n"
        f"💵 طلبات السحب المعلقة: {stats['pending_withdrawals']}"
    )
    await update.message.reply_text(message, parse_mode="Markdown")

# Add task conversation
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 غير مصرح.")
        return ConversationHandler.END
    
    await update.message.reply_text("📝 أرسل عنوان المهمة:")
    return ADD_TASK_TITLE

async def add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['task_title'] = update.message.text
    await update.message.reply_text("🔗 أرسل رابط المهمة (أو أرسل 'تخطي'):")
    return ADD_TASK_URL

async def add_task_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if url.lower() == 'تخطي':
        url = ""
    title = context.user_data.get('task_title')
    
    success = await add_task(title, url, TASK_REWARD)
    if success:
        await update.message.reply_text(f"✅ تم إضافة المهمة \"{title}\" بنجاح!")
    else:
        await update.message.reply_text("❌ فشل إضافة المهمة.")
    
    return ConversationHandler.END

async def cancel_add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء إضافة المهمة.")
    return ConversationHandler.END

# Delete task conversation
async def delete_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 غير مصرح.")
        return ConversationHandler.END
    
    tasks = await get_tasks()
    if not tasks:
        await update.message.reply_text("📭 لا توجد مهام لحذفها.")
        return ConversationHandler.END
    
    keyboard = []
    for task in tasks:
        keyboard.append([InlineKeyboardButton(f"❌ {task['title']}", callback_data=f"del_{task['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_del")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🗑 اختر مهمة للحذف:", reply_markup=reply_markup)
    return DELETE_TASK_SELECT

async def delete_task_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_del":
        await query.edit_message_text("❌ تم إلغاء حذف المهمة.")
        return ConversationHandler.END
    
    task_id = int(query.data.split("_")[1])
    success = await delete_task(task_id)
    if success:
        await query.edit_message_text("✅ تم حذف المهمة بنجاح!")
    else:
        await query.edit_message_text("❌ فشل حذف المهمة.")
    
    return ConversationHandler.END

async def cancel_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء حذف المهمة.")
    return ConversationHandler.END

# Broadcast conversation
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🚫 غير مصرح.")
        return ConversationHandler.END
    
    await update.message.reply_text("📢 أرسل الرسالة التي تريد إذاعتها لجميع المستخدمين:")
    return BROADCAST_MESSAGE

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message_text = update.message.text
    user_id = update.effective_user.id
    
    status_msg = await update.message.reply_text("⏳ جاري إرسال الإذاعة...")
    
    count = await broadcast_message(message_text, context.bot)
    await status_msg.edit_text(f"✅ تم إرسال الإذاعة إلى {count} مستخدم.")
    return ConversationHandler.END

async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء الإذاعة.")
    return ConversationHandler.END

# Withdrawal requests handling
async def admin_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending withdrawal requests for admin"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    
    requests = await get_withdraw_requests("pending")
    if not requests:
        await update.message.reply_text("📭 لا توجد طلبات سحب معلقة.")
        return
    
    for req in requests:
        user = await get_user(req["user_id"])
        username = user["username"] if user else "غير معروف"
        message = (
            f"🧾 **طلب سحب #{req['id']}**\n"
            f"👤 المستخدم: {username} (ID: {req['user_id']})\n"
            f"💰 المبلغ: {req['amount']} نقطة\n"
            f"📅 التاريخ: {req['created_at']}"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ قبول", callback_data=f"approve_{req['id']}"),
                InlineKeyboardButton("❌ رفض", callback_data=f"reject_{req['id']}")
            ]
        ])
        await update.message.reply_text(message, parse_mode="Markdown", reply_markup=keyboard)

async def withdrawal_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle approve/reject of withdrawal"""
    query = update.callback_query
    await query.answer()
    
    action, request_id = query.data.split("_")
    request_id = int(request_id)
    
    if action == "approve":
        success = await update_withdraw_request(request_id, "approved", admin_approve=True)
        if success:
            await query.edit_message_text("✅ تم قبول طلب السحب وخصم الرصيد.")
        else:
            await query.edit_message_text("⚠️ فشل قبول الطلب (رصيد غير كافٍ أو خطأ).")
    elif action == "reject":
        await update_withdraw_request(request_id, "rejected", admin_approve=False)
        await query.edit_message_text("❌ تم رفض طلب السحب.")
    else:
        await query.edit_message_text("⚠️ إجراء غير معروف.")

# ========== MAIN APPLICATION ==========
async def main():
    # Initialize Supabase
    await init_supabase()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add conversation handlers
    add_task_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ إضافة مهمة$"), add_task_start)],
        states={
            ADD_TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_title)],
            ADD_TASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_task_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel_add_task), MessageHandler(filters.Regex("^🔙"), cancel_add_task)],
    )
    
    delete_task_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^❌ حذف مهمة$"), delete_task_start)],
        states={
            DELETE_TASK_SELECT: [CallbackQueryHandler(delete_task_select, pattern="^(del_|cancel_del)")],
        },
        fallbacks=[CommandHandler("cancel", cancel_delete)],
    )
    
    broadcast_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📢 إذاعة$"), broadcast_start)],
        states={
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel_broadcast), MessageHandler(filters.Regex("^🔙"), cancel_broadcast)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Regex("^(📋 المهام|💰 رصيدي|👥 دعوة الأصدقاء|💸 سحب الأرباح|⚙️ لوحة التحكم|🔙 العودة للرئيسية)$"), main_menu_handler))
    application.add_handler(CallbackQueryHandler(task_callback, pattern="^task_"))
    application.add_handler(CallbackQueryHandler(complete_task_callback, pattern="^complete_"))
    application.add_handler(CallbackQueryHandler(back_to_tasks_callback, pattern="^back_to_tasks$"))
    application.add_handler(CallbackQueryHandler(withdrawal_action_callback, pattern="^(approve_|reject_)"))
    
    # Admin handlers
    application.add_handler(MessageHandler(filters.Regex("^📊 الإحصائيات$"), admin_stats))
    application.add_handler(MessageHandler(filters.Regex("^💵 طلبات السحب$"), admin_withdrawals))
    application.add_handler(add_task_conv)
    application.add_handler(delete_task_conv)
    application.add_handler(broadcast_conv)
    
    # Fallback for unknown messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: u.message.reply_text("❓ استخدم الأزرار في القائمة.")))
    
    # Start polling
    logger.info("Bot started...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    if not all([BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY, ADMIN_ID]):
        logger.error("Missing environment variables! Set BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY, ADMIN_ID")
        exit(1)
    asyncio.run(main())
