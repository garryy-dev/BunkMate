import os
import math
import logging
import zipfile
import glob
import shutil
import csv
import io
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

from bunkmate.data_manager import (
    load_data, save_data, get_all_users, delete_user,
    is_banned, ban_user, unban_user, kill_switch, backup_data, DATA_DIR
)
from bunkmate.calculator import get_attendance_pct, classes_can_bunk, classes_must_attend, status_emoji, projected_bunks

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

# Conversation states
ADDING_SUBJECT = 1
SETTING_TARGET = 2
RENAMING_NEW_NAME = 3
IMPORT_PRESENT = 4
IMPORT_ABSENT = 5
IMPORT_CANCELLED = 6
FORECAST_DATE = 7
ONBOARDING_NAME = 8
ONBOARDING_SEM = 9
ONBOARDING_ROLL = 10

def is_admin(user_id: str) -> bool:
    return ADMIN_ID and user_id == ADMIN_ID

# --- Helper to create subject selection keyboards ---
def get_subjects_keyboard(data: dict, prefix: str) -> InlineKeyboardMarkup:
    keyboard = []
    for name in data["subjects"]:
        keyboard.append([InlineKeyboardButton(name, callback_data=f"{prefix}_{name}")])
    return InlineKeyboardMarkup(keyboard)

# --- Gatekeeper ---
async def require_onboarding(update: Update) -> bool:
    user_id = str(update.effective_user.id)
    if is_banned(user_id): return True # Silently ignore banned users
    
    data = load_data(user_id)
    if not (data.get("real_name") and data.get("college_roll") and data.get("current_semester")):
        msg = "🛑 Please complete setup first!\nSend /start to begin onboarding."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        elif update.message:
            await update.message.reply_text(msg)
        return True
    return False

# --- Admin Commands ---
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not is_admin(user_id): return
    
    msg = (
        "🛠 *ADMIN CHEAT SHEET* 🛠\n\n"
        "• `/admin` - View the global dashboard and user list.\n"
        "• `/admin_help` - Show this menu.\n"
        "• `/admin_snoop <id>` - View a specific user's private dashboard.\n"
        "• `/admin_broadcast <msg>` - Send an announcement to EVERY user.\n"
        "• `/admin_backup` - Instantly download a zip file of all JSON data.\n"
        "• `/admin_ban <id>` - Permanently delete a user and block them forever.\n"
        "• `/admin_unban <id>` - Remove a user from the ban blacklist.\n"
        "• `/admin_delete_user <id>` - Permanently delete a specific user's data.\n"
        "• `/admin_kill_switch` - ☢️ WIPE THE ENTIRE DATABASE.\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not is_admin(user_id): return
    
    users = get_all_users()
    total_users = len(users)
    total_subjects = 0
    total_bunks = 0
    total_classes = 0
    
    user_list = []
    
    for d in users:
        uid = d.get("user_id", "Unknown")
        
        real_name = d.get("real_name", "Unknown")
        sem = d.get("current_semester", "Unknown")
        uname = d.get("tg_username", "None")
        
        user_list.append(f"`{uid}` | {real_name} | {sem} | @{uname}")
        
        for subj, info in d.get("subjects", {}).items():
            total_subjects += 1
            total_bunks += info.get("absent", 0)
            total_classes += (info.get("present", 0) + info.get("absent", 0))
            
    msg = f"👑 *ADMIN DASHBOARD* 👑\n\n"
    msg += f"👥 *Total Users:* {total_users}\n"
    msg += f"📚 *Total Subjects Tracked:* {total_subjects}\n"
    msg += f"🛌 *Global Classes Bunked:* {total_bunks} / {total_classes}\n\n"
    msg += f"📋 *User List (ID | Name | Sem | Username):*\n" + "\n".join(user_list)
    
    await update.message.reply_text(msg, parse_mode="Markdown")

async def admin_snoop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not is_admin(user_id): return
    
    if not context.args:
        await update.message.reply_text("Usage: /admin_snoop <user_id>")
        return
        
    target_id = context.args[0]
    d = load_data(target_id)
    if not d.get("real_name"):
        await update.message.reply_text("User ID not found or not onboarded.")
        return
        
    d = load_data(target_id)
    msg = f"🕵️‍♂️ *Snooping on {d.get('real_name', 'Unknown')}* 🕵️‍♂️\n\n"
    for name, info in d.get("subjects", {}).items():
        p = info["present"]
        a = info["absent"]
        pct = get_attendance_pct(p, a)
        msg += f"- *{name}*: {pct:.1f}% (P:{p} A:{a})\n"
        
    await update.message.reply_text(msg, parse_mode="Markdown")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not is_admin(user_id): return
    
    if not context.args:
        await update.message.reply_text("Usage: /admin_broadcast <message>")
        return
        
    broadcast_msg = " ".join(context.args)
    users = get_all_users()
    sent = 0
    for d in users:
        uid = d.get("user_id")
        if not uid or is_banned(uid): continue
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 *ANNOUNCEMENT*\n\n{broadcast_msg}", parse_mode="Markdown")
            sent += 1
        except Exception:
            pass
            
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users.")

async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not is_admin(user_id): return
    
    zip_path = os.path.join(DATA_DIR, "backup.zip")
    backup_data(zip_path)
                    
    with open(zip_path, 'rb') as f:
        await update.message.reply_document(document=f, filename="BunkMate_Backup.zip")
        
    os.remove(zip_path)

async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not is_admin(user_id): return
    
    if not context.args:
        await update.message.reply_text("Usage: /admin_ban <user_id>")
        return
        
    target_id = context.args[0]
    ban_user(target_id)
        
    await update.message.reply_text(f"🔨 User {target_id} has been banned. Their data is safely quarantined.")

async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not is_admin(user_id): return
    
    if not context.args:
        await update.message.reply_text("Usage: /admin_unban <user_id>")
        return
        
    target_id = context.args[0]
    unban_user(target_id)
        
    await update.message.reply_text(f"✅ User {target_id} has been unbanned. Their original data has been restored.")

async def admin_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not is_admin(user_id): return
    
    if not context.args:
        await update.message.reply_text("Usage: /admin_delete_user <user_id>")
        return
        
    target_id = context.args[0]
    deleted = delete_user(target_id)
        
    if deleted:
        await update.message.reply_text(f"✅ User {target_id}'s data was permanently deleted.")
    else:
        await update.message.reply_text(f"⚠ Could not find data for user {target_id}.")

async def admin_kill_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if not is_admin(user_id): return
    
    keyboard = [
        [InlineKeyboardButton("💀 YES, INITIATE KILL SWITCH", callback_data="killswitch_1_yes")],
        [InlineKeyboardButton("NO, ABORT", callback_data="del_no")]
    ]
    await update.message.reply_text(
        "☢️ *KILL SWITCH INITIATED* ☢️\nAre you absolutely sure you want to delete EVERY USER'S DATA? This will wipe the entire bot.", 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode="Markdown"
    )

# --- Commands ---
async def start_onboarding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    if is_banned(user_id): return ConversationHandler.END
    data = load_data(user_id)
    
    changed = False
    if update.effective_user.first_name and data.get("tg_first_name") != update.effective_user.first_name:
        data["tg_first_name"] = update.effective_user.first_name
        changed = True
    if update.effective_user.username and data.get("tg_username") != update.effective_user.username:
        data["tg_username"] = update.effective_user.username
        changed = True
        
    if changed:
        save_data(data, print_msg=False, user_id=user_id)
        
    if data.get("real_name") and data.get("college_roll") and data.get("current_semester"):
        await update.message.reply_text(
            f"👋 Welcome back, {data['real_name']}!\n"
            "Use the blue Menu button to track attendance and bunk safely."
        )
        return ConversationHandler.END
        
    await update.message.reply_text("👋 Welcome to BunkMate!\nPlease enter your Name :")
    return ONBOARDING_NAME

async def onboarding_name_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    real_name = update.message.text.strip()
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    data["real_name"] = real_name
    save_data(data, print_msg=False, user_id=user_id)
    
    await update.message.reply_text(f"Nice to meet you, {real_name}! What is your college roll number?")
    return ONBOARDING_ROLL

async def onboarding_roll_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    college_roll = update.message.text.strip()
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    data["college_roll"] = college_roll
    save_data(data, print_msg=False, user_id=user_id)
    
    await update.message.reply_text("Got it! Which semester are you currently in? (e.g., type 3 for Semester 3)")
    return ONBOARDING_SEM

async def onboarding_sem_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    sem_raw = update.message.text.strip()
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    
    if sem_raw.isdigit():
        data["current_semester"] = f"Semester {sem_raw}"
    else:
        data["current_semester"] = sem_raw
        
    save_data(data, print_msg=False, user_id=user_id)
    
    await update.message.reply_text("✅ Onboarding complete!\nYou can now use the blue Menu button to add your subjects and track attendance.")
    return ConversationHandler.END

async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await require_onboarding(update): return
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    
    if not data["subjects"]:
        await update.message.reply_text("No subjects found. Use /add to add one.")
        return
    
    # Feature 9: Welcome Back greeting
    hour = datetime.now().hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"
    real_name = data.get("real_name", "")
    
    # Feature 6: Streak counter
    streak = _calculate_streak(data)
    streak_text = f"\n🔥 *{streak}-day logging streak!*\n" if streak >= 2 else ""
        
    global_target = data["target_percentage"]
    msg = f"👋 {greeting}, {real_name}!\n"
    msg += f"📊 *BunkMate Dashboard* (Target: {global_target}%){streak_text}\n"
    total_present = 0
    total_absent = 0
    
    for name, info in data["subjects"].items():
        p = info["present"]
        a = info["absent"]
        c = info.get("cancelled", 0)
        total_present += p
        total_absent += a
        
        subj_target = info.get("target", global_target)
        target_display = f" [Target: {subj_target}%]" if "target" in info else ""
        
        pct = get_attendance_pct(p, a)
        emoji = status_emoji(pct, subj_target)
        can_bunk = classes_can_bunk(p, a, subj_target)
        must_go = classes_must_attend(p, a, subj_target)
        
        is_ended = False
        has_projection = False
        remaining_classes = 0
        schedule = info.get("schedule")
        if schedule and "end_date" in schedule:
            try:
                end_str = schedule["end_date"]
                end_d = datetime.fromisoformat(end_str).date() if "T" in end_str else date.fromisoformat(end_str)
                if end_d <= date.today():
                    is_ended = True
                else:
                    if "absolute_total" in schedule:
                        absolute_total = schedule["absolute_total"]
                        remaining_classes = max(0, absolute_total - (p + a + c))
                        has_projection = True
                    else:
                        weeks_remaining = max(0, (end_d - date.today()).days / 7.0)
                        per_week = float(schedule.get("per_week", 1))
                        remaining_classes = math.ceil(weeks_remaining * per_week)
                        has_projection = True
            except ValueError:
                pass
                
        msg += f"{emoji} *{name}*{target_display} ({pct:.1f}%)\n"
        # Feature 2: Show cancelled count
        msg += f"P: {p} | A: {a} | C: {c}\n"
        
        if is_ended:
            msg += f"🏁 Semester ended\n\n"
        elif has_projection and remaining_classes > 0:
            projection = projected_bunks(p, a, subj_target, remaining_classes)
            if projection["status"] == "impossible":
                msg += f"💀 Cannot reach target even if you attend all {remaining_classes} remaining classes!\n\n"
            else:
                # Feature 8: Grammar fix
                cls_word = "class" if projection['can_bunk'] == 1 else "classes"
                rem_word = "class" if remaining_classes == 1 else "classes"
                msg += f"🔮 Forecast: Can bunk {projection['can_bunk']} {cls_word} out of {remaining_classes} remaining {rem_word}\n\n"
        else:
            if can_bunk > 0:
                # Feature 8: Grammar fix
                cls_word = "class" if can_bunk == 1 else "classes"
                msg += f"🛌 Can bunk {can_bunk} {cls_word}\n"
            if must_go > 0 and must_go != float("inf"):
                cls_word = "class" if must_go == 1 else "classes"
                msg += f"📚 Must attend {must_go} {cls_word}\n"
            msg += "\n"
        
    overall = get_attendance_pct(total_present, total_absent)
    msg += f"📈 *Overall Attendance:* {overall:.1f}%"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

# Feature 6: Streak helper
def _calculate_streak(data: dict) -> int:
    """Count consecutive days with at least one logged entry."""
    all_dates = set()
    for subj, info in data.get("subjects", {}).items():
        for entry in info.get("history", []):
            try:
                dt = datetime.fromisoformat(entry["date"]).date()
                all_dates.add(dt)
            except (ValueError, KeyError):
                pass
    if not all_dates:
        return 0
    streak = 0
    check_date = date.today()
    while check_date in all_dates:
        streak += 1
        check_date -= timedelta(days=1)
    return streak

async def mark_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await require_onboarding(update): return
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    if not data["subjects"]:
        await update.message.reply_text("No subjects to mark. Use /add first.")
        return
    await update.message.reply_text("Select subject to mark:", reply_markup=get_subjects_keyboard(data, "marksubj"))

async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await require_onboarding(update): return
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    if not data["subjects"]:
        await update.message.reply_text("No subjects. Use /add first.")
        return
    await update.message.reply_text("Select subject to view history:", reply_markup=get_subjects_keyboard(data, "histsubj"))

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await require_onboarding(update): return
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    if not data["subjects"]:
        await update.message.reply_text("No subjects. Use /add first.")
        return
    await update.message.reply_text("Select subject to REMOVE:", reply_markup=get_subjects_keyboard(data, "remsubj"))

async def delete_account_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await require_onboarding(update): return
    keyboard = [
        [InlineKeyboardButton("⚠ YES, DELETE MY ACCOUNT", callback_data="delacc_1_yes")],
        [InlineKeyboardButton("NO, CANCEL", callback_data="del_no")]
    ]
    await update.message.reply_text(
        "🛑 *WARNING* 🛑\nAre you sure you want to delete your entire BunkMate account?\nThis will permanently delete all your subjects and attendance history.", 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode="Markdown"
    )

# --- Inline Button Handlers ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await require_onboarding(update): return
    query = update.callback_query
    await query.answer()
    data_str = query.data
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    
    # --- Mark Attendance Routing ---
    if data_str.startswith("marksubj_"):
        subj_name = data_str[9:]
        keyboard = [
            [InlineKeyboardButton("✅ Present", callback_data=f"log_P_{subj_name}")],
            [InlineKeyboardButton("❌ Absent", callback_data=f"log_A_{subj_name}")],
            [InlineKeyboardButton("⊘ Cancelled", callback_data=f"log_C_{subj_name}")]
        ]
        await query.edit_message_text(f"Marking attendance for: *{subj_name}*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    elif data_str.startswith("log_") or data_str.startswith("forcelog_"):
        is_force = data_str.startswith("forcelog_")
        parts = data_str.split("_", 2)
        status = parts[1]
        subj_name = parts[2]
        
        if subj_name not in data["subjects"]:
            await query.edit_message_text("Subject not found.")
            return
            
        subject = data["subjects"][subj_name]
        today = datetime.now()
        today_str = today.date().isoformat()
        
        if not is_force:
            already_logged = any(entry.get("date", "").startswith(today_str) for entry in subject.get("history", []))
            if already_logged:
                keyboard = [
                    [InlineKeyboardButton("⚠ YES, log 2nd class", callback_data=f"forcelog_{status}_{subj_name}")],
                    [InlineKeyboardButton("NO, cancel", callback_data="del_no")]
                ]
                await query.edit_message_text(f"⚠ You already marked attendance for *{subj_name}* today!\nAre you sure you want to log 2nd class?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                return
        
        status_word = "Present"
        if status == "P": subject["present"] += 1
        elif status == "A": 
            subject["absent"] += 1
            status_word = "Absent"
        elif status == "C": 
            subject["cancelled"] += 1
            status_word = "Cancelled"
            
        subject["history"].append({"date": today.isoformat(), "status": status_word})
        save_data(data, print_msg=False, user_id=user_id)
        
        # Feature 1: Undo button
        keyboard = [[InlineKeyboardButton("↩️ Undo", callback_data=f"undo_{status}_{subj_name}")]]
        await query.edit_message_text(
            f"✅ Marked *{status_word}* for *{subj_name}*.", 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode="Markdown"
        )

    # --- History Routing ---
    elif data_str.startswith("histsubj_"):
        subj_name = data_str[9:]
        if subj_name not in data["subjects"]: return
        history = data["subjects"][subj_name].get("history", [])
        if not history:
            await query.edit_message_text(f"No history for {subj_name}.")
            return
        
        msg = f"📜 *History for {subj_name}*\n"
        for entry in history[-15:]: # Show last 15
            try:
                dt = datetime.fromisoformat(entry["date"])
                date_str = dt.strftime("%d-%b-%y %H:%M")
            except:
                date_str = entry["date"]
            msg += f"• {date_str} - {entry['status']}\n"
        await query.edit_message_text(msg, parse_mode="Markdown")

    # --- Remove Routing ---
    elif data_str.startswith("remsubj_"):
        subj_name = data_str[8:]
        keyboard = [
            [InlineKeyboardButton("⚠ YES, DELETE IT", callback_data=f"del_yes_{subj_name}")],
            [InlineKeyboardButton("NO, CANCEL", callback_data="del_no")]
        ]
        await query.edit_message_text(f"Are you sure you want to delete *{subj_name}*?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    elif data_str.startswith("del_yes_"):
        subj_name = data_str[8:]
        if subj_name in data["subjects"]:
            del data["subjects"][subj_name]
            save_data(data, print_msg=False, user_id=user_id)
            await query.edit_message_text(f"🗑 Deleted *{subj_name}*.", parse_mode="Markdown")
            
    elif data_str == "delacc_1_yes":
        keyboard = [
            [InlineKeyboardButton("🔥 I AM ABSOLUTELY SURE", callback_data="delacc_2_yes")],
            [InlineKeyboardButton("NO, GET ME OUT OF HERE", callback_data="del_no")]
        ]
        await query.edit_message_text(
            "🛑 *FINAL WARNING* 🛑\nThere is no going back. This data cannot be recovered. Are you absolutely sure?", 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode="Markdown"
        )
        
    elif data_str == "delacc_2_yes":
        delete_user(user_id)
        await query.edit_message_text("✅ Your account and all associated data have been permanently deleted.\nSend /start to create a new account.")
            
    elif data_str == "killswitch_1_yes":
        if not is_admin(user_id): return
        keyboard = [
            [InlineKeyboardButton("💣 DESTROY EVERYTHING", callback_data="killswitch_2_yes")],
            [InlineKeyboardButton("ABORT ABORT", callback_data="del_no")]
        ]
        await query.edit_message_text(
            "☢️ *FINAL WARNING* ☢️\nThis will instantly delete ALL users, ALL attendance data, and ALL banned data. IT CANNOT BE UNDONE.", 
            reply_markup=InlineKeyboardMarkup(keyboard), 
            parse_mode="Markdown"
        )
        
    elif data_str == "killswitch_2_yes":
        if not is_admin(user_id): return
        kill_switch()
        await query.edit_message_text("✅ *KILL SWITCH EXECUTED.* All databases have been permanently wiped.", parse_mode="Markdown")

    elif data_str == "del_no":
        await query.edit_message_text("Cancelled.")

    # Feature 1: Undo handler
    elif data_str.startswith("undo_"):
        parts = data_str.split("_", 2)
        status = parts[1]
        subj_name = parts[2]
        
        if subj_name not in data["subjects"]:
            await query.edit_message_text("Subject not found. Cannot undo.")
            return
            
        subject = data["subjects"][subj_name]
        
        # Reverse the counter
        if status == "P" and subject["present"] > 0:
            subject["present"] -= 1
        elif status == "A" and subject["absent"] > 0:
            subject["absent"] -= 1
        elif status == "C" and subject.get("cancelled", 0) > 0:
            subject["cancelled"] -= 1
        else:
            await query.edit_message_text("Nothing to undo.")
            return
            
        # Remove the last matching history entry
        if subject.get("history"):
            subject["history"].pop()
            
        save_data(data, print_msg=False, user_id=user_id)
        status_map = {"P": "Present", "A": "Absent", "C": "Cancelled"}
        await query.edit_message_text(f"↩️ Undone *{status_map.get(status, status)}* for *{subj_name}*.", parse_mode="Markdown")


# --- New Feature Commands ---

# Feature 4: /help
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "📖 *BunkMate Help Guide*\n\n"
        "*📊 Tracking*\n"
        "/dashboard — View your attendance overview\n"
        "/mark — Log Present, Absent, or Cancelled\n"
        "/history — View past attendance for a subject\n"
        "/stats — See this week's performance summary\n\n"
        "*📚 Subjects*\n"
        "/add — Add a new subject\n"
        "/remove — Delete a subject\n"
        "/rename — Rename a subject\n"
        "/import — Import existing attendance for a subject\n\n"
        "*🎯 Targets & Forecasts*\n"
        "/set\\_target — Change your global target %\n"
        "/target\\_subject — Set a custom target for one subject\n"
        "/forecast — Set semester end date for projections\n\n"
        "*⚙️ Other*\n"
        "/export — Download your attendance as a CSV file\n"
        "/reminder — Toggle daily attendance reminders\n"
        "/help — Show this guide\n"
        "/delete\\_account — Permanently delete your data\n"
        "/cancel — Cancel any active action\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

# Feature 5: /stats (Weekly Summary)
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await require_onboarding(update): return
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    
    if not data["subjects"]:
        await update.message.reply_text("No subjects found.")
        return
    
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Monday
    
    total_p = 0
    total_a = 0
    total_c = 0
    best_subj = None
    best_pct = -1
    worst_subj = None
    worst_pct = 101
    
    msg = "📈 *Weekly Summary* (" + week_start.strftime("%d %b") + " – " + today.strftime("%d %b") + ")\n\n"
    
    for name, info in data["subjects"].items():
        wp = 0
        wa = 0
        wc = 0
        for entry in info.get("history", []):
            try:
                dt = datetime.fromisoformat(entry["date"]).date()
                if dt >= week_start and dt <= today:
                    if entry["status"] == "Present": wp += 1
                    elif entry["status"] == "Absent": wa += 1
                    elif entry["status"] == "Cancelled": wc += 1
            except (ValueError, KeyError):
                pass
        
        week_total = wp + wa
        if week_total > 0:
            pct = (wp / week_total) * 100
            if pct > best_pct:
                best_pct = pct
                best_subj = name
            if pct < worst_pct:
                worst_pct = pct
                worst_subj = name
            msg += f"• *{name}*: P: {wp} | A: {wa} | C: {wc} ({pct:.0f}%)\n"
        else:
            msg += f"• *{name}*: No classes logged this week\n"
        
        total_p += wp
        total_a += wa
        total_c += wc
    
    week_total_all = total_p + total_a
    if week_total_all > 0:
        week_pct = (total_p / week_total_all) * 100
        msg += f"\n📊 *Overall This Week:* {total_p}/{week_total_all} ({week_pct:.0f}%)\n"
    else:
        msg += "\nNo classes logged this week yet.\n"
    
    if best_subj and best_pct >= 0:
        msg += f"🏆 *Best:* {best_subj} ({best_pct:.0f}%)\n"
    if worst_subj and worst_pct <= 100 and worst_subj != best_subj:
        msg += f"⚠️ *Needs Work:* {worst_subj} ({worst_pct:.0f}%)\n"
    
    # Streak
    streak = _calculate_streak(data)
    if streak >= 2:
        msg += f"\n🔥 *{streak}-day logging streak!* Keep it up!"
    
    await update.message.reply_text(msg, parse_mode="Markdown")

# Feature 7: /export (CSV)
async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await require_onboarding(update): return
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    
    if not data["subjects"]:
        await update.message.reply_text("No subjects to export.")
        return
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Subject", "Present", "Absent", "Cancelled", "Attendance %"])
    
    for name, info in data["subjects"].items():
        p = info["present"]
        a = info["absent"]
        c = info.get("cancelled", 0)
        pct = get_attendance_pct(p, a)
        writer.writerow([name, p, a, c, f"{pct:.1f}%"])
    
    # Add an empty row and then history
    writer.writerow([])
    writer.writerow(["--- Detailed History ---"])
    writer.writerow(["Subject", "Date", "Status"])
    
    for name, info in data["subjects"].items():
        for entry in info.get("history", []):
            try:
                dt = datetime.fromisoformat(entry["date"])
                date_str = dt.strftime("%d-%b-%Y %H:%M")
            except (ValueError, KeyError):
                date_str = entry.get("date", "Unknown")
            writer.writerow([name, date_str, entry.get("status", "Unknown")])
    
    output.seek(0)
    bio = io.BytesIO(output.getvalue().encode('utf-8'))
    bio.name = f"BunkMate_Export_{date.today().isoformat()}.csv"
    
    await update.message.reply_document(document=bio, filename=bio.name, caption="📁 Here is your attendance data!")

# Feature 3: /reminder (Daily Reminder Toggle)
async def reminder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await require_onboarding(update): return
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    
    current = data.get("reminder_enabled", False)
    data["reminder_enabled"] = not current
    save_data(data, print_msg=False, user_id=user_id)
    
    if data["reminder_enabled"]:
        await update.message.reply_text("🔔 Daily reminders *enabled*!\nI will send you a nudge at 6 PM every day to log your attendance.", parse_mode="Markdown")
    else:
        await update.message.reply_text("🔕 Daily reminders *disabled*.", parse_mode="Markdown")

async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job that runs daily at 6 PM to remind users."""
    users = get_all_users()
    for d in users:
        uid = d.get("user_id")
        if not uid or is_banned(uid): continue
        if not d.get("reminder_enabled", False): continue
        
        real_name = d.get("real_name", "there")
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📝 Hey {real_name}! Have you logged your attendance today?\nTap /mark to do it now, or /dashboard to check your stats!"
            )
        except Exception:
            pass


# --- Conversation Handlers ---
async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Action cancelled.")
    return ConversationHandler.END

# Add Subject
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await require_onboarding(update): return ConversationHandler.END
    await update.message.reply_text("Enter the new subject name (or type /cancel):")
    return ADDING_SUBJECT

async def add_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    name = update.message.text.strip()
    data = load_data(user_id)
    
    if any(s.lower() == name.lower() for s in data["subjects"]):
        await update.message.reply_text("Subject already exists. Cancelled.")
        return ConversationHandler.END
        
    data["subjects"][name] = {"present": 0, "absent": 0, "cancelled": 0, "history": []}
    save_data(data, print_msg=False, user_id=user_id)
    await update.message.reply_text(f"✅ Subject '{name}' added!")
    return ConversationHandler.END

# Set Target
async def target_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await require_onboarding(update): return ConversationHandler.END
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    await update.message.reply_text(f"Current target is {data['target_percentage']}%. Enter new target (e.g. 65.5):")
    return SETTING_TARGET

async def target_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = float(update.message.text.strip())
        if val <= 0 or val > 100: raise ValueError
        user_id = str(update.effective_user.id)
        data = load_data(user_id)
        data["target_percentage"] = val
        save_data(data, print_msg=False, user_id=user_id)
        await update.message.reply_text(f"🎯 Target updated to {val}%!")
    except ValueError:
        await update.message.reply_text("Invalid percentage. Must be 1-100. Cancelled.")
    return ConversationHandler.END

# Target Subject
async def target_subj_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await require_onboarding(update): return ConversationHandler.END
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    if not data["subjects"]:
        await update.message.reply_text("No subjects found.")
        return ConversationHandler.END
    subs = "\n".join(f"- {s}" for s in data["subjects"])
    await update.message.reply_text(f"Subjects:\n{subs}\n\nType the EXACT name of the subject to set a custom target for (or /cancel):")
    return 12

async def target_subj_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    subj = update.message.text.strip()
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    
    actual_name = next((s for s in data["subjects"] if s.lower() == subj.lower()), None)
    if not actual_name:
        await update.message.reply_text("Subject not found. Cancelled.")
        return ConversationHandler.END
        
    context.user_data['target_subj'] = actual_name
    await update.message.reply_text(f"What should be the target percentage for '{actual_name}'? (e.g., 50 or 0):")
    return 13

async def target_subj_pct_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    try:
        pct = float(raw)
        if pct < 0 or pct > 100: raise ValueError
        
        subj = context.user_data['target_subj']
        user_id = str(update.effective_user.id)
        data = load_data(user_id)
        
        data["subjects"][subj]["target"] = pct
        save_data(data, print_msg=False, user_id=user_id)
        
        await update.message.reply_text(f"✔ Target for {subj} updated to {pct}%.")
    except ValueError:
        await update.message.reply_text("Invalid percentage. Must be between 0 and 100. Cancelled.")
    return ConversationHandler.END

# Import Existing Attendance
async def import_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await require_onboarding(update): return ConversationHandler.END
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    if not data["subjects"]:
        await update.message.reply_text("No subjects found.")
        return ConversationHandler.END
    subs = "\n".join(f"- {s}" for s in data["subjects"])
    await update.message.reply_text(f"Subjects:\n{subs}\n\nType the EXACT name of the subject you want to import attendance for (or /cancel):")
    return 14

async def import_subj_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    subj = update.message.text.strip()
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    
    actual_name = next((s for s in data["subjects"] if s.lower() == subj.lower()), None)
    if not actual_name:
        await update.message.reply_text("Subject not found. Cancelled.")
        return ConversationHandler.END
        
    context.user_data['import_subj'] = actual_name
    await update.message.reply_text(f"How many classes have you been PRESENT for in '{actual_name}'?")
    return IMPORT_PRESENT

async def import_present_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = int(update.message.text.strip())
        if val < 0: raise ValueError
        context.user_data['import_p'] = val
        await update.message.reply_text(f"Present: {val}. Now, how many classes have you been ABSENT for?")
        return IMPORT_ABSENT
    except ValueError:
        await update.message.reply_text("Invalid number. Cancelled.")
        return ConversationHandler.END

async def import_absent_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = int(update.message.text.strip())
        if val < 0: raise ValueError
        context.user_data['import_a'] = val
        await update.message.reply_text(f"Absent: {val}. Finally, how many classes were CANCELLED?")
        return IMPORT_CANCELLED
    except ValueError:
        await update.message.reply_text("Invalid number. Cancelled.")
        return ConversationHandler.END

async def import_cancelled_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        val = int(update.message.text.strip())
        if val < 0: raise ValueError
        
        subj = context.user_data['import_subj']
        p = context.user_data['import_p']
        a = context.user_data['import_a']
        c = val
        
        user_id = str(update.effective_user.id)
        data = load_data(user_id)
        
        data["subjects"][subj]["present"] = p
        data["subjects"][subj]["absent"] = a
        data["subjects"][subj]["cancelled"] = c
        save_data(data, print_msg=False, user_id=user_id)
        
        await update.message.reply_text(f"✔ Attendance imported for {subj}!\nP: {p} | A: {a} | C: {c}")
    except ValueError:
        await update.message.reply_text("Invalid number. Cancelled.")
    return ConversationHandler.END


# Rename Subject 
async def rename_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await require_onboarding(update): return ConversationHandler.END
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    if not data["subjects"]:
        await update.message.reply_text("No subjects.")
        return ConversationHandler.END
        
    subs = "\n".join(f"- {s}" for s in data["subjects"])
    await update.message.reply_text(f"Subjects:\n{subs}\n\nType the EXACT name of the subject you want to rename (or /cancel):")
    return 1 # State 1: wait for old name

async def rename_old_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    old_name = update.message.text.strip()
    
    actual_name = next((s for s in data["subjects"] if s.lower() == old_name.lower()), None)
    if not actual_name:
        await update.message.reply_text("Subject not found. Cancelled.")
        return ConversationHandler.END
        
    context.user_data['rename_old'] = actual_name
    await update.message.reply_text(f"Enter the NEW name for '{actual_name}':")
    return 2 # State 2: wait for new name

async def rename_new_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_name = update.message.text.strip()
    old_name = context.user_data.get('rename_old')
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    
    if any(s.lower() == new_name.lower() and s.lower() != old_name.lower() for s in data["subjects"]):
        await update.message.reply_text(f"Name '{new_name}' already exists. Cancelled.")
        return ConversationHandler.END
        
    # Preserve order
    new_subjects = {}
    for k, v in data["subjects"].items():
        if k == old_name: new_subjects[new_name] = v
        else: new_subjects[k] = v
    data["subjects"] = new_subjects
    save_data(data, print_msg=False, user_id=user_id)
    
    await update.message.reply_text(f"✔ Renamed '{old_name}' to '{new_name}'.")
    return ConversationHandler.END


# Forecast
async def forecast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await require_onboarding(update): return ConversationHandler.END
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    if not data["subjects"]:
        await update.message.reply_text("No subjects.")
        return ConversationHandler.END
    subs = "\n".join(f"- {s}" for s in data["subjects"])
    await update.message.reply_text(f"Subjects:\n{subs}\n\nType the EXACT name of the subject for the forecast (or /cancel):")
    return 1

async def forecast_subj_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    data = load_data(user_id)
    subj = update.message.text.strip()
    
    actual_name = next((s for s in data["subjects"] if s.lower() == subj.lower()), None)
    if not actual_name:
        await update.message.reply_text("Not found. Cancelled.")
        return ConversationHandler.END
        
    context.user_data['forecast_subj'] = actual_name
    await update.message.reply_text(f"Enter the End Date for '{actual_name}' (Format: DD-MM-YYYY):")
    return 2

async def forecast_date_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    try:
        dt = datetime.strptime(raw, "%d-%m-%Y").date()
        context.user_data['forecast_end_date'] = dt.isoformat()
        await update.message.reply_text("How many times a week does this class occur? (e.g. 3)")
        return 3
    except ValueError:
        await update.message.reply_text("Invalid format. Use DD-MM-YYYY. Cancelled.")
        return ConversationHandler.END

async def forecast_per_week_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    try:
        per_week = float(raw)
        if per_week <= 0: raise ValueError
        
        subj = context.user_data['forecast_subj']
        end_date = context.user_data['forecast_end_date']
        user_id = str(update.effective_user.id)
        data = load_data(user_id)
        
        end_d = date.fromisoformat(end_date)
        weeks_remaining = max(0, (end_d - date.today()).days / 7.0)
        future_classes = math.ceil(weeks_remaining * per_week)
        
        info = data["subjects"][subj]
        p = info.get("present", 0)
        a = info.get("absent", 0)
        c = info.get("cancelled", 0)
        absolute_total = p + a + c + future_classes
        
        data["subjects"][subj]["schedule"] = {
            "end_date": end_date, 
            "per_week": per_week,
            "absolute_total": absolute_total
        }
        save_data(data, print_msg=False, user_id=user_id)
        
        await update.message.reply_text(f"✔ Forecast set for {subj} (ends {end_d.strftime('%d-%m-%Y')}, {per_week} classes/week).")
    except ValueError:
        await update.message.reply_text("Invalid number. Must be greater than 0. Cancelled.")
    return ConversationHandler.END


# Setup Bot Menu
async def post_init(application: Application) -> None:
    commands = [
        BotCommand("dashboard", "View attendance and bunk margins"),
        BotCommand("mark", "Log attendance"),
        BotCommand("add", "Add a new subject"),
        BotCommand("import", "Import existing attendance"),
        BotCommand("stats", "This week's performance summary"),
        BotCommand("export", "Download attendance as CSV"),
        BotCommand("history", "View class history"),
        BotCommand("remove", "Delete a subject"),
        BotCommand("rename", "Rename a subject"),
        BotCommand("set_target", "Change target percentage"),
        BotCommand("target_subject", "Set custom target for a subject"),
        BotCommand("forecast", "Set semester end date for a subject"),
        BotCommand("reminder", "Toggle daily reminders"),
        BotCommand("help", "Show all commands"),
        BotCommand("delete_account", "Delete your BunkMate account"),
        BotCommand("cancel", "Cancel current action")
    ]
    await application.bot.set_my_commands(commands)
    logging.info("Bot commands menu registered.")

def main() -> None:
    if not TOKEN or TOKEN == "your_bot_token_here":
        print("Please set your TELEGRAM_BOT_TOKEN in .env")
        return
        
    try:
        application = Application.builder().token(TOKEN).post_init(post_init).build()
        _has_job_queue = True
    except TypeError:
        # APScheduler not installed — build without job_queue
        application = Application.builder().token(TOKEN).post_init(post_init).job_queue(None).build()
        _has_job_queue = False
        logging.warning("APScheduler not installed. Daily reminders disabled. Run: pip install python-telegram-bot[job-queue]")

    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('start', start_onboarding)],
        states={
            ONBOARDING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboarding_name_receive)],
            ONBOARDING_ROLL: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboarding_roll_receive)],
            ONBOARDING_SEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboarding_sem_receive)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)],
    ))
    
    # Admin commands
    application.add_handler(CommandHandler("admin", admin_dashboard))
    application.add_handler(CommandHandler("admin_help", admin_help))
    application.add_handler(CommandHandler("admin_snoop", admin_snoop))
    application.add_handler(CommandHandler("admin_broadcast", admin_broadcast))
    application.add_handler(CommandHandler("admin_backup", admin_backup))
    application.add_handler(CommandHandler("admin_ban", admin_ban))
    application.add_handler(CommandHandler("admin_unban", admin_unban))
    application.add_handler(CommandHandler("admin_delete_user", admin_delete_user))
    application.add_handler(CommandHandler("admin_kill_switch", admin_kill_switch))
    
    # User commands
    application.add_handler(CommandHandler("dashboard", dashboard))
    application.add_handler(CommandHandler("mark", mark_cmd))
    application.add_handler(CommandHandler("history", history_cmd))
    application.add_handler(CommandHandler("remove", remove_cmd))
    application.add_handler(CommandHandler("delete_account", delete_account_cmd))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("stats", stats_cmd))
    application.add_handler(CommandHandler("export", export_cmd))
    application.add_handler(CommandHandler("reminder", reminder_cmd))
    
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('add', add_start)],
        states={ADDING_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive)]},
        fallbacks=[CommandHandler('cancel', cancel_conv)],
    ))
    
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('import', import_start)],
        states={
            14: [MessageHandler(filters.TEXT & ~filters.COMMAND, import_subj_receive)],
            IMPORT_PRESENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, import_present_receive)],
            IMPORT_ABSENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, import_absent_receive)],
            IMPORT_CANCELLED: [MessageHandler(filters.TEXT & ~filters.COMMAND, import_cancelled_receive)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)],
    ))
    
    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('set_target', target_start)],
        states={SETTING_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, target_receive)]},
        fallbacks=[CommandHandler('cancel', cancel_conv)],
    ))

    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('target_subject', target_subj_start)],
        states={
            12: [MessageHandler(filters.TEXT & ~filters.COMMAND, target_subj_receive)],
            13: [MessageHandler(filters.TEXT & ~filters.COMMAND, target_subj_pct_receive)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)],
    ))

    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('rename', rename_start)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_old_receive)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, rename_new_receive)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)],
    ))

    application.add_handler(ConversationHandler(
        entry_points=[CommandHandler('forecast', forecast_start)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, forecast_subj_receive)],
            2: [MessageHandler(filters.TEXT & ~filters.COMMAND, forecast_date_receive)],
            3: [MessageHandler(filters.TEXT & ~filters.COMMAND, forecast_per_week_receive)]
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)],
    ))
    
    application.add_handler(CallbackQueryHandler(button_handler))

    # Feature 3: Schedule daily reminder job at 6 PM IST (12:30 PM UTC)
    if _has_job_queue and application.job_queue:
        from datetime import time as dt_time
        application.job_queue.run_daily(
            send_daily_reminders,
            time=dt_time(hour=12, minute=30, second=0),  # 6:00 PM IST = 12:30 PM UTC
            name="daily_reminder"
        )
        logging.info("Daily reminder job scheduled.")

    RENDER = os.environ.get("RENDER", False)

    if RENDER:
        PORT = int(os.environ.get("PORT", 10000))
        
        # Start a simple HTTP server in a background thread to satisfy Render's port requirement
        # and to give cron-job.org a 200 OK response to keep the bot awake!
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        
        class HealthCheckHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Bot is alive and awake!")
                
            def log_message(self, format, *args):
                pass # Suppress logs to keep terminal clean
                
        def run_dummy_server():
            server = HTTPServer(("0.0.0.0", PORT), HealthCheckHandler)
            server.serve_forever()
            
        threading.Thread(target=run_dummy_server, daemon=True).start()
        logging.info(f"Health check server listening on port {PORT}")

    # Use polling for both local and Render. It's much more reliable and avoids webhook errors!
    print("Bot is running in Polling mode... Press Ctrl+C to stop.")
    application.run_polling()

if __name__ == '__main__':
    main()
