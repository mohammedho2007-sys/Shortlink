import asyncio
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv
from supabase import create_async_client
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse
import uvicorn

# Load environment variables
load_dotenv()

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
WEBSITE_URL = os.getenv("WEBSITE_URL")

if not all([BOT_TOKEN, ADMIN_ID, SUPABASE_URL, SUPABASE_KEY, WEBSITE_URL]):
    raise ValueError("Missing required environment variables")

# Constants
REFERRAL_POINTS = 50
MIN_WITHDRAWAL = 1000
POINTS_TO_USD = 1000  # 1000 points = 1 USD

# Conversation states
ADD_TASK_TITLE, ADD_TASK_URL, ADD_TASK_REWARD = range(3)
DELETE_TASK_SELECT = range(1)
WITHDRAW_AMOUNT = range(1)
BROADCAST_MESSAGE = range(1)

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Supabase client
supabase = create_async_client(SUPABASE_URL, SUPABASE_KEY)

# ============= Database Functions =============

async def register_user(user_id: int, username: str = None, referred_by: int = None) -> Dict:
    """Register a new user if not exists, handle referral points"""
    try:
        # Check if user exists
        result = await supabase.table("users").select("*").eq("user_id", user_id).execute()
        if result.data:
            return result.data[0]
        
        # Register new user
        user_data = {
            "user_id": user_id,
            "username": username,
            "points": 0,
            "referred_by": referred_by,
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Insert user
        result = await supabase.table("users").insert(user_data).execute()
        new_user = result.data[0]
        
        # Give referral points if referred by someone
        if referred_by and referred_by != user_id:
            # Check if referrer exists and hasn't already received points for this referral
            referrer = await supabase.table("users").select("*").eq("user_id", referred_by).execute()
            if referrer.data:
                # Add points to referrer
                await supabase.table("users").update({
                    "points": referrer.data[0]["points"] + REFERRAL_POINTS
                }).eq("user_id", referred_by).execute()
                
                # Log referral (optional: could create a referrals table)
                logger.info(f"Referral: {referred_by} got {REFERRAL_POINTS} points for inviting {user_id}")
        
        return new_user
    except Exception as e:
        logger.error(f"Error registering user {user_id}: {e}")
        raise

async def get_user(user_id: int) -> Optional[Dict]:
    """Get user data"""
    try:
        result = await supabase.table("users").select("*").eq("user_id", user_id).execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}")
        return None

async def add_points(user_id: int, points: int) -> bool:
    """Add points to user"""
    try:
        result = await supabase.table("users").select("points").eq("user_id", user_id).execute()
        if not result.data:
            return False
        current_points = result.data[0]["points"]
        await supabase.table("users").update({
            "points": current_points + points
        }).eq("user_id", user_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error adding points to {user_id}: {e}")
        return False

async def complete_task(user_id: int, task_id: int) -> bool:
    """Mark task as completed and award points"""
    try:
        # Check if already completed
        completed = await supabase.table("completed_tasks").select("*").eq("user_id", user_id).eq("task_id", task_id).execute()
        if completed.data:
            return False
        
        # Get task reward
        task = await supabase.table("tasks").select("reward").eq("id", task_id).eq("active", True).execute()
        if not task.data:
            return False
        
        reward = task.data[0]["reward"]
        
        # Add points to user
        if not await add_points(user_id, reward):
            return False
        
        # Record completion
        await supabase.table("completed_tasks").insert({
            "user_id": user_id,
            "task_id": task_id,
            "completed_at": datetime.utcnow().isoformat()
        }).execute()
        
        return True
    except Exception as e:
        logger.error(f"Error completing task {task_id} for user {user_id}: {e}")
        return False

async def get_active_tasks() -> List[Dict]:
    """Get all active tasks"""
    try:
        result = await supabase.table("tasks").select("*").eq("active", True).order("id").execute()
        return result.data
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        return []

async def add_task(title: str, destination_url: str, reward: int) -> bool:
    """Add a new task"""
    try:
        await supabase.table("tasks").insert({
            "title": title,
            "destination_url": destination_url,
            "reward": reward,
            "active": True
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Error adding task: {e}")
        return False

async def delete_task(task_id: int) -> bool:
    """Delete a task (soft delete by setting active=False)"""
    try:
        await supabase.table("tasks").update({"active": False}).eq("id", task_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {e}")
        return False

async def get_total_users() -> int:
    """Get total number of users"""
    try:
        result = await supabase.table("users").select("user_id", count="exact").execute()
        return result.count
    except Exception as e:
        logger.error(f"Error getting user count: {e}")
        return 0

async def get_total_points() -> int:
    """Get sum of all user points"""
    try:
        result = await supabase.table("users").select("points").execute()
        return sum(user["points"] for user in result.data) if result.data else 0
    except Exception as e:
        logger.error(f"Error getting total points: {e}")
        return 0

async def get_completed_tasks_count() -> int:
    """Get total number of completed tasks"""
    try:
        result = await supabase.table("completed_tasks").select("*", count="exact").execute()
        return result.count
    except Exception as e:
        logger.error(f"Error getting completed tasks count: {e}")
        return 0

async def create_withdrawal(user_id: int, amount: int) -> bool:
    """Create a withdrawal request"""
    try:
        # Check user balance
        user = await get_user(user_id)
        if not user or user["points"] < amount:
            return False
        
        # Create request
        await supabase.table("withdraw_requests").insert({
            "user_id": user_id,
            "amount": amount,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat()
        }).execute()
        
        # Deduct points immediately (or after approval - we'll deduct after approval to be safe)
        # Better to deduct after approval to avoid issues
        return True
    except Exception as e:
        logger.error(f"Error creating withdrawal for {user_id}: {e}")
        return False

async def get_pending_withdrawals() -> List[Dict]:
    """Get all pending withdrawal requests"""
    try:
        result = await supabase.table("withdraw_requests").select("*").eq("status", "pending").order("created_at").execute()
        return result.data
    except Exception as e:
        logger.error(f"Error fetching withdrawals: {e}")
        return []

async def update_withdrawal_status(request_id: int, status: str, user_id: int = None) -> bool:
    """Update withdrawal request status and deduct points if approved"""
    try:
        if status == "approved" and user_id:
            # Get request details
            req = await supabase.table("withdraw_requests").select("*").eq("id", request_id).execute()
            if req.data:
                amount = req.data[0]["amount"]
                # Deduct points from user
                user = await get_user(user_id)
                if user:
                    new_points = user["points"] - amount
                    await supabase.table("users").update({"points": new_points}).eq("user_id", user_id).execute()
        
        await supabase.table("withdraw_requests").update({"status": status}).eq("id", request_id).execute()
        return True
    except Exception as e:
        logger.error(f"Error updating withdrawal {request_id}: {e}")
        return False

async def get_all_users() -> List[Dict]:
    """Get all users for broadcasting"""
    try:
        result = await supabase.table("users").select("user_id, username").execute()
        return result.data
    except Exception as e:
        logger.error(f"Error fetching all users: {e}")
        return []

# ============= Helper Functions =============

def get_main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Get main menu keyboard"""
    keyboard = [
        [InlineKeyboardButton("📋 المهام", callback_data="menu_tasks")],
        [InlineKeyboardButton("💰 رصيدي", callback_data="menu_balance")],
        [InlineKeyboardButton("👥 دعوة الأصدقاء", callback_data="menu_referral")],
        [InlineKeyboardButton("💸 سحب الأرباح", callback_data="menu_withdraw")]
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

def get_admin_panel() -> InlineKeyboardMarkup:
    """Get admin panel keyboard"""
    keyboard = [
        [InlineKeyboardButton("➕ إضافة مهمة", callback_data="admin_add_task")],
        [InlineKeyboardButton("❌ حذف مهمة", callback_data="admin_delete_task")],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 إذاعة", callback_data="admin_broadcast")],
        [InlineKeyboardButton("💵 طلبات السحب", callback_data="admin_withdrawals")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ============= Bot Handlers =============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with referral support"""
    user = update.effective_user
    user_id = user.id
    username = user.username
    
    # Parse referral from command
    referred_by = None
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referred_by = int(arg.split("_")[1])
                if referred_by == user_id:
                    referred_by = None
            except ValueError:
                pass
    
    # Register user
    await register_user(user_id, username, referred_by)
    
    # Send welcome message
    welcome_text = (
        f"🌟 مرحباً بك {user.first_name} في بوت المكافآت!\n\n"
        f"📌 أكمل المهام واحصل على نقاط\n"
        f"💵 1000 نقطة = 1 دولار\n"
        f"👥 ادعو أصدقائك واحصل على {REFERRAL_POINTS} نقطة لكل صديق\n\n"
        f"استخدم الأزرار أدناه للبدء:"
    )
    
    is_admin = user_id == ADMIN_ID
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu(is_admin)
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle menu callback queries"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "menu_tasks":
        await show_tasks(query, user_id)
    elif data == "menu_balance":
        await show_balance(query, user_id)
    elif data == "menu_referral":
        await show_referral(query, context, user_id)
    elif data == "menu_withdraw":
        await start_withdraw(query, user_id, context)
    elif data == "admin_panel":
        if user_id == ADMIN_ID:
            await query.edit_message_text(
                "⚙️ لوحة التحكم - اختر إجراء:",
                reply_markup=get_admin_panel()
            )
        else:
            await query.edit_message_text("⛔ غير مصرح به")
    elif data == "back_to_main":
        await query.edit_message_text(
            "القائمة الرئيسية:",
            reply_markup=get_main_menu(user_id == ADMIN_ID)
        )

async def show_tasks(query, user_id: int):
    """Show active tasks"""
    tasks = await get_active_tasks()
    if not tasks:
        await query.edit_message_text("📭 لا توجد مهام متاحة حالياً", reply_markup=get_main_menu(False))
        return
    
    keyboard = []
    for task in tasks:
        verification_url = f"{WEBSITE_URL}/reward?user_id={user_id}&task_id={task['id']}"
        keyboard.append([
            InlineKeyboardButton(
                f"📌 {task['title']} - {task['reward']} نقطة",
                url=verification_url
            )
        ])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")])
    
    await query.edit_message_text(
        "📋 المهام المتاحة:\n\nاضغط على أي مهمة لفتح الرابط وإكمالها",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_balance(query, user_id: int):
    """Show user balance"""
    user = await get_user(user_id)
    if not user:
        await query.edit_message_text("⚠️ حدث خطأ، يرجى المحاولة لاحقاً")
        return
    
    points = user["points"]
    usd = points / POINTS_TO_USD
    
    balance_text = (
        f"💰 رصيدك:\n\n"
        f"📊 النقاط: {points}\n"
        f"💵 الدولار: ${usd:.2f}\n\n"
        f"💡 1000 نقطة = 1 دولار\n"
        f"💸 الحد الأدنى للسحب: {MIN_WITHDRAWAL} نقطة"
    )
    
    await query.edit_message_text(
        balance_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
    )

async def show_referral(query, context, user_id: int):
    """Show referral information and link"""
    bot_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    referral_text = (
        f"👥 دعوة الأصدقاء:\n\n"
        f"🎁 احصل على {REFERRAL_POINTS} نقطة لكل صديق يدعوه عبر رابطك\n\n"
        f"🔗 رابط الدعوة الخاص بك:\n"
        f"`{referral_link}`\n\n"
        f"📤 شارك الرابط مع أصدقائك لكسب النقاط!"
    )
    
    await query.edit_message_text(
        referral_text,
        parse_mode="MARKDOWN",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 نسخ الرابط", callback_data="copy_link")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]
        ])
    )

async def copy_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle copy link callback"""
    query = update.callback_query
    await query.answer("✅ تم نسخ الرابط!", show_alert=True)

async def start_withdraw(query, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Start withdrawal process"""
    user = await get_user(user_id)
    if not user:
        await query.edit_message_text("⚠️ حدث خطأ")
        return
    
    points = user["points"]
    if points < MIN_WITHDRAWAL:
        await query.edit_message_text(
            f"❌ لا يمكنك السحب حالياً\n\n"
            f"رصيدك: {points} نقطة\n"
            f"الحد الأدنى: {MIN_WITHDRAWAL} نقطة\n\n"
            f"💡 أكمل المهام لزيادة رصيدك",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_to_main")]])
        )
        return
    
    await query.edit_message_text(
        f"💰 رصيدك المتاح: {points} نقطة\n"
        f"💵 يعادل: ${points/POINTS_TO_USD:.2f}\n\n"
        f"📝 أدخل المبلغ الذي تريد سحبه (بالنقاط):\n"
        f"الحد الأدنى: {MIN_WITHDRAWAL}\n"
        f"الحد الأقصى: {points}\n\n"
        f"❗ أرسل رقم فقط (مثال: 1000)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="back_to_main")]])
    )
    return WITHDRAW_AMOUNT

async def process_withdraw_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process withdrawal amount input"""
    user_id = update.effective_user.id
    try:
        amount = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال رقم صحيح")
        return WITHDRAW_AMOUNT
    
    user = await get_user(user_id)
    if not user or user["points"] < amount or amount < MIN_WITHDRAWAL:
        await update.message.reply_text(
            f"❌ المبلغ غير صالح\n"
            f"الحد الأدنى: {MIN_WITHDRAWAL}\n"
            f"الحد الأقصى: {user['points'] if user else 0}"
        )
        return WITHDRAW_AMOUNT
    
    # Create withdrawal request
    if await create_withdrawal(user_id, amount):
        await update.message.reply_text(
            f"✅ تم تقديم طلب السحب بنجاح!\n"
            f"المبلغ: {amount} نقطة (${amount/POINTS_TO_USD:.2f})\n\n"
            f"سيتم مراجعة طلبك من قبل الإدارة قريباً.",
            reply_markup=get_main_menu(user_id == ADMIN_ID)
        )
        # Notify admin
        await context.bot.send_message(
            ADMIN_ID,
            f"💰 طلب سحب جديد!\n"
            f"👤 المستخدم: {user_id}\n"
            f"💵 المبلغ: {amount} نقطة (${amount/POINTS_TO_USD:.2f})"
        )
    else:
        await update.message.reply_text("❌ حدث خطأ، يرجى المحاولة لاحقاً")
    
    return ConversationHandler.END

# ============= Admin Handlers =============

async def admin_add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add task process"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📝 أرسل عنوان المهمة:")
    return ADD_TASK_TITLE

async def admin_add_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task title"""
    context.user_data["task_title"] = update.message.text
    await update.message.reply_text("🔗 أرسل رابط المهمة (destination URL):")
    return ADD_TASK_URL

async def admin_add_task_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task URL"""
    context.user_data["task_url"] = update.message.text
    await update.message.reply_text("🎁 أرسل قيمة المكافأة (نقاط، الافتراضي 5):\n(أرسل رقم فقط)")
    return ADD_TASK_REWARD

async def admin_add_task_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get task reward and save"""
    try:
        reward = int(update.message.text.strip())
    except ValueError:
        reward = 5
    
    title = context.user_data["task_title"]
    url = context.user_data["task_url"]
    
    if await add_task(title, url, reward):
        await update.message.reply_text(
            f"✅ تم إضافة المهمة بنجاح!\n\n"
            f"العنوان: {title}\n"
            f"الرابط: {url}\n"
            f"المكافأة: {reward} نقطة",
            reply_markup=get_main_menu(True)
        )
    else:
        await update.message.reply_text("❌ حدث خطأ أثناء إضافة المهمة")
    
    return ConversationHandler.END

async def admin_delete_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show tasks to delete"""
    query = update.callback_query
    await query.answer()
    
    tasks = await get_active_tasks()
    if not tasks:
        await query.edit_message_text("📭 لا توجد مهام لحذفها")
        return ConversationHandler.END
    
    keyboard = []
    for task in tasks:
        keyboard.append([InlineKeyboardButton(
            f"{task['title']} - {task['reward']} نقطة",
            callback_data=f"delete_task_{task['id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 إلغاء", callback_data="back_to_main")])
    
    await query.edit_message_text(
        "❌ اختر مهمة للحذف:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return DELETE_TASK_SELECT

async def admin_delete_task_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm task deletion"""
    query = update.callback_query
    await query.answer()
    
    task_id = int(query.data.split("_")[2])
    if await delete_task(task_id):
        await query.edit_message_text("✅ تم حذف المهمة بنجاح")
    else:
        await query.edit_message_text("❌ حدث خطأ أثناء الحذف")
    
    # Show main menu
    await query.message.reply_text("القائمة الرئيسية:", reply_markup=get_main_menu(True))
    return ConversationHandler.END

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show statistics"""
    query = update.callback_query
    await query.answer()
    
    total_users = await get_total_users()
    total_points = await get_total_points()
    completed_tasks = await get_completed_tasks_count()
    total_usd = total_points / POINTS_TO_USD
    
    stats_text = (
        f"📊 إحصائيات البوت:\n\n"
        f"👥 عدد المستخدمين: {total_users}\n"
        f"💰 إجمالي النقاط: {total_points}\n"
        f"💵 إجمالي الدولار: ${total_usd:.2f}\n"
        f"✅ المهام المكتملة: {completed_tasks}\n"
        f"📈 متوسط النقاط لكل مستخدم: {total_points/total_users if total_users > 0 else 0:.1f}"
    )
    
    await query.edit_message_text(
        stats_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]])
    )

async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast process"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📢 أرسل الرسالة التي تريد إذاعتها لجميع المستخدمين:")
    return BROADCAST_MESSAGE

async def admin_broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send broadcast message"""
    message_text = update.message.text
    await update.message.reply_text("⏳ جاري إرسال الرسالة...")
    
    users = await get_all_users()
    success_count = 0
    fail_count = 0
    
    for user in users:
        try:
            await context.bot.send_message(user["user_id"], f"📢 إعلان:\n\n{message_text}")
            success_count += 1
            await asyncio.sleep(0.05)  # Small delay to avoid flooding
        except Exception as e:
            fail_count += 1
            logger.error(f"Failed to send to {user['user_id']}: {e}")
    
    await update.message.reply_text(
        f"✅ تم إرسال الإذاعة!\n"
        f"تم التسليم: {success_count}\n"
        f"فشل: {fail_count}\n"
        f"إجمالي: {len(users)}",
        reply_markup=get_main_menu(True)
    )
    return ConversationHandler.END

async def admin_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show pending withdrawals"""
    query = update.callback_query
    await query.answer()
    
    withdrawals = await get_pending_withdrawals()
    if not withdrawals:
        await query.edit_message_text("📭 لا توجد طلبات سحب معلقة")
        return
    
    keyboard = []
    for w in withdrawals:
        keyboard.append([InlineKeyboardButton(
            f"طلب #{w['id']} - {w['amount']} نقطة - مستخدم: {w['user_id']}",
            callback_data=f"withdraw_{w['id']}_{w['user_id']}"
        )])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")])
    
    await query.edit_message_text(
        "💵 طلبات السحب المعلقة:\n\nاختر طلباً للموافقة أو الرفض:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_withdraw_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle withdrawal action (approve/reject)"""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    request_id = int(parts[1])
    user_id = int(parts[2])
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ موافقة", callback_data=f"approve_{request_id}_{user_id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject_{request_id}_{user_id}")
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_withdrawals")]
    ])
    
    await query.edit_message_text(
        f"طلب سحب #{request_id}\n"
        f"المستخدم: {user_id}\n"
        f"اختر الإجراء:",
        reply_markup=keyboard
    )

async def admin_withdraw_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve withdrawal"""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    request_id = int(parts[1])
    user_id = int(parts[2])
    
    if await update_withdrawal_status(request_id, "approved", user_id):
        await query.edit_message_text("✅ تمت الموافقة على طلب السحب")
        # Notify user
        await context.bot.send_message(
            user_id,
            f"✅ تمت الموافقة على طلب السحب الخاص بك!\n"
            f"سيتم إرسال المبلغ إلى حسابك قريباً."
        )
    else:
        await query.edit_message_text("❌ حدث خطأ")
    
    # Show admin panel
    await query.message.reply_text("لوحة التحكم:", reply_markup=get_admin_panel())

async def admin_withdraw_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reject withdrawal"""
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    request_id = int(parts[1])
    user_id = int(parts[2])
    
    if await update_withdrawal_status(request_id, "rejected", None):
        await query.edit_message_text("❌ تم رفض طلب السحب")
        # Notify user
        await context.bot.send_message(
            user_id,
            f"❌ لقد تم رفض طلب السحب الخاص بك.\n"
            f"يرجى التواصل مع الإدارة لمزيد من المعلومات."
        )
    else:
        await query.edit_message_text("❌ حدث خطأ")
    
    # Show admin panel
    await query.message.reply_text("لوحة التحكم:", reply_markup=get_admin_panel())

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current conversation"""
    await update.message.reply_text(
        "❌ تم الإلغاء",
        reply_markup=get_main_menu(update.effective_user.id == ADMIN_ID)
    )
    return ConversationHandler.END

# ============= FastAPI Web Server =============

app = FastAPI()

@app.get("/reward")
async def reward_endpoint(user_id: int, task_id: int):
    """Handle task completion verification"""
    try:
        # Check if user exists
        user = await get_user(user_id)
        if not user:
            return HTMLResponse("<h3>⚠️ مستخدم غير موجود</h3><p>يرجى تسجيل الدخول إلى البوت أولاً</p>")
        
        # Check if task already completed
        completed = await supabase.table("completed_tasks").select("*").eq("user_id", user_id).eq("task_id", task_id).execute()
        if completed.data:
            return HTMLResponse("<h3>✅ تم إكمال هذه المهمة مسبقاً</h3><p>لقد حصلت على مكافأتك بالفعل</p>")
        
        # Get task
        task = await supabase.table("tasks").select("*").eq("id", task_id).eq("active", True).execute()
        if not task.data:
            return HTMLResponse("<h3>⚠️ المهمة غير موجودة أو غير نشطة</h3>")
        
        # Complete task and award points
        if await complete_task(user_id, task_id):
            reward = task.data[0]["reward"]
            return HTMLResponse(f"""
            <html>
            <head><title>تم إكمال المهمة</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h2>✅ تم إكمال المهمة بنجاح!</h2>
                <p>لقد حصلت على {reward} نقطة</p>
                <p>يمكنك العودة إلى البوت لمواصلة المهام</p>
                <a href="https://t.me/{(await supabase.table('users').select('*').limit(1).execute()).data[0].get('username', '')}">العودة إلى البوت</a>
            </body>
            </html>
            """)
        else:
            return HTMLResponse("<h3>⚠️ حدث خطأ أثناء إكمال المهمة</h3>")
    except Exception as e:
        logger.error(f"Reward endpoint error: {e}")
        return HTMLResponse("<h3>⚠️ حدث خطأ داخلي</h3>")

# ============= Main Function =============

async def main():
    """Main function to run the bot and web server"""
    # Create bot application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    
    # Add conversation handlers for admin functions
    add_task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_task_start, pattern="^admin_add_task$")],
        states={
            ADD_TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_task_title)],
            ADD_TASK_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_task_url)],
            ADD_TASK_REWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_task_reward)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    
    delete_task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_delete_task_start, pattern="^admin_delete_task$")],
        states={
            DELETE_TASK_SELECT: [CallbackQueryHandler(admin_delete_task_confirm, pattern="^delete_task_")],
        },
        fallbacks=[],
    )
    
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_broadcast_start, pattern="^admin_broadcast$")],
        states={
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    
    withdraw_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_withdraw, pattern="^menu_withdraw$")],
        states={
            WITHDRAW_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_withdraw_amount)],
        },
        fallbacks=[CallbackQueryHandler(menu_callback, pattern="^back_to_main$")],
    )
    
    # Add all handlers
    application.add_handler(add_task_conv)
    application.add_handler(delete_task_conv)
    application.add_handler(broadcast_conv)
    application.add_handler(withdraw_conv)
    
    # Callback handlers
    application.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_"))
    application.add_handler(CallbackQueryHandler(copy_link_callback, pattern="^copy_link$"))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    application.add_handler(CallbackQueryHandler(admin_withdrawals, pattern="^admin_withdrawals$"))
    application.add_handler(CallbackQueryHandler(admin_withdraw_action, pattern="^withdraw_"))
    application.add_handler(CallbackQueryHandler(admin_withdraw_approve, pattern="^approve_"))
    application.add_handler(CallbackQueryHandler(admin_withdraw_reject, pattern="^reject_"))
    
    # Set bot commands
    await application.bot.set_my_commands([
        ("start", "بدء البوت"),
        ("menu", "القائمة الرئيسية"),
    ])
    
    # Start bot polling
    async with application:
        await application.start()
        logger.info("Bot started successfully")
        
        # Start web server
        port = int(os.getenv("PORT", 8000))
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        
        # Run both bot and web server concurrently
        await asyncio.gather(
            application.updater.start_polling(),
            server.serve()
        )

if __name__ == "__main__":
    asyncio.run(main())
