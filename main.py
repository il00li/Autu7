import os
import sqlite3
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import google.generativeai as genai

# إعداد تسجيل الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# التوكنات والإعدادات (من متغيرات البيئة)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8110119856:AAEKyEiIlpHP2e-xOQym0YHkGEBLRgyG_wA')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'AIzaSyAEULfP5zi5irv4yRhFugmdsjBoLk7kGsE')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '7251748706'))
BOT_USERNAME = os.environ.get('BOT_USERNAME', '@SEAK7_BOT')
WEBHOOK_URL = "https://autu7.onrender.com"  # رابط الويب هوك الخاص بك

# تهيئة Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

# إعداد قاعدة البيانات
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bot_db.sqlite')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, 
                 join_date TEXT, invited_by INTEGER DEFAULT 0)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS vip_members
                 (user_id INTEGER PRIMARY KEY, start_date TEXT, 
                 end_date TEXT, invites_required INTEGER DEFAULT 2,
                 is_permanent INTEGER DEFAULT 0)''')  # إضافة عمود للعضوية الدائمة
    
    c.execute('''CREATE TABLE IF NOT EXISTS referrals
                 (referral_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  referrer_id INTEGER, referee_id INTEGER,
                  date TEXT, is_active INTEGER DEFAULT 1)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS required_channels
                 (channel_id TEXT PRIMARY KEY, channel_username TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bot_settings
                 (id INTEGER PRIMARY KEY, 
                  free_trial_days INTEGER DEFAULT 7,
                  initial_invites_required INTEGER DEFAULT 10)''')
    
    c.execute("INSERT OR IGNORE INTO bot_settings VALUES (1, 7, 10)")
    conn.commit()
    conn.close()

init_db()

# ========== دوال المساعدة ==========
def get_setting(setting_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"SELECT {setting_name} FROM bot_settings WHERE id = 1")
    result = c.fetchone()[0]
    conn.close()
    return result

def register_user(user, invited_by=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?)",
              (user.id, user.username, user.first_name, datetime.now().isoformat(), invited_by))
    conn.commit()
    
    # إذا كان المستخدم هو المدير، منحه عضوية VIP دائمة
    if user.id == ADMIN_ID:
        # التحقق مما إذا كان المدير لديه عضوية دائمة بالفعل
        c.execute("SELECT * FROM vip_members WHERE user_id = ?", (ADMIN_ID,))
        existing = c.fetchone()
        
        if not existing:
            c.execute("""INSERT OR REPLACE INTO vip_members 
                      VALUES (?, ?, ?, ?, ?)""",
                      (ADMIN_ID, datetime.now().isoformat(), 
                       "9999-12-31", 0, 1))  # 1 تعني عضوية دائمة
            conn.commit()
            logger.info(f"Granted permanent VIP to admin: {ADMIN_ID}")
    
    conn.close()

def check_vip_status(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT end_date, is_permanent FROM vip_members WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result:
        end_date, is_permanent = result
        # إذا كانت العضوية دائمة
        if is_permanent == 1:
            return True
        
        # إذا كانت العضوية مؤقتة ولم تنتهي بعد
        if end_date and datetime.fromisoformat(end_date) > datetime.now():
            return True
    
    return False

async def check_channel_subscription(user_id, bot):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT channel_id FROM required_channels")
    channels = c.fetchall()
    conn.close()
    
    for channel in channels:
        try:
            member = await bot.get_chat_member(chat_id=channel[0], user_id=user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            logger.error(f"Error checking channel subscription: {e}")
            return False
    return True

def get_required_channels():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT channel_id, channel_username FROM required_channels")
    channels = c.fetchall()
    conn.close()
    return channels

def check_and_grant_vip(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("""SELECT COUNT(*) FROM referrals 
              WHERE referrer_id = ? AND is_active = 1""", (user_id,))
    active_refs = c.fetchone()[0]
    
    initial_invites = get_setting('initial_invites_required')
    trial_days = get_setting('free_trial_days')

    if active_refs >= initial_invites:
        start_date = datetime.now()
        end_date = start_date + timedelta(days=trial_days)
        
        c.execute("""INSERT OR REPLACE INTO vip_members 
                  VALUES (?, ?, ?, ?, ?)""",
                  (user_id, start_date.isoformat(), 
                   end_date.isoformat(), 2, 0))  # 0 تعني عضوية غير دائمة
        
        conn.commit()
    
    conn.close()

# ========== دوال جديدة للمدير ==========
def grant_vip(user_id, days=None, permanent=False):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    start_date = datetime.now()
    
    if permanent:
        end_date = "9999-12-31"  # تاريخ بعيد جداً لتمثيل الديمومة
        is_permanent = 1
    else:
        end_date = (start_date + timedelta(days=days)).isoformat()
        is_permanent = 0
    
    c.execute("""INSERT OR REPLACE INTO vip_members 
              VALUES (?, ?, ?, ?, ?)""",
              (user_id, start_date.isoformat(), end_date, 0, is_permanent))
    
    conn.commit()
    conn.close()
    return True

def get_user_info(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

# ========== واجهة المستخدم ==========
def main_keyboard(user_id):
    keyboard = [
        [InlineKeyboardButton("💻 كتابة كود", callback_data='write_code')],
        [InlineKeyboardButton("🧪 اختبار كود", callback_data='test_code')],
        [InlineKeyboardButton("🤖 توليد مشروع بوت", callback_data='generate_bot')],
        [InlineKeyboardButton("❓ سؤال Gemini", callback_data='ask_gemini')]
    ]
    
    if check_vip_status(user_id):
        keyboard.append([InlineKeyboardButton("👑 عضوية VIP (فعالة)", callback_data='vip_status')])
    else:
        keyboard.append([InlineKeyboardButton("💎 الحصول على VIP", callback_data='get_vip')])
    
    if user_id == ADMIN_ID:
        keyboard.append([InlineKeyboardButton("🛠 لوحة المدير", callback_data='admin_panel')])
    
    return InlineKeyboardMarkup(keyboard)

# ========== معالجة الأوامر والرسائل ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        args = context.args
        
        invited_by = 0
        if args and args[0].startswith('ref_'):
            try:
                invited_by = int(args[0][4:])
                register_user(user, invited_by)
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("INSERT INTO referrals (referrer_id, referee_id, date) VALUES (?, ?, ?)",
                          (invited_by, user.id, datetime.now().isoformat()))
                conn.commit()
                conn.close()
                
                check_and_grant_vip(invited_by)
                
            except ValueError as ve:
                logger.error(f"Value error in referral: {ve}")
        
        register_user(user)  # سيسجل المستخدم ويعطي المدير عضوية دائمة
        
        if not await check_channel_subscription(user.id, context.bot):
            channels = get_required_channels()
            message = "❗ يجب الاشتراك في القنوات التالية:\n"
            for channel in channels:
                message += f"- @{channel[1]}\n"
            message += "\nبعد الاشتراك اضغط /start"
            await update.message.reply_text(message)
            return
        
        welcome_msg = f"""
        🚀 مرحبًا {user.first_name} في بوت المطورين!
        
        اختر أحد الخيارات من القائمة:
        """
        await update.message.reply_text(welcome_msg, reply_markup=main_keyboard(user.id))
    
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ غير متوقع. الرجاء المحاولة لاحقًا.")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        user_message = update.message.text
        
        # معالجة إهداء VIP من المدير
        if user_id == ADMIN_ID and context.user_data.get('awaiting_user_id_for_gift'):
            try:
                target_user_id = int(user_message)
                gift_type = context.user_data['gift_type']
                
                if gift_type == 'permanent':
                    grant_vip(target_user_id, permanent=True)
                    message = f"✅ تم منح عضوية VIP الدائمة للمستخدم {target_user_id}"
                else:
                    days = int(gift_type)
                    grant_vip(target_user_id, days=days)
                    message = f"✅ تم منح عضوية VIP لمدة {days} يوم للمستخدم {target_user_id}"
                
                await update.message.reply_text(message)
                context.user_data.pop('awaiting_user_id_for_gift', None)
                context.user_data.pop('gift_type', None)
                return
            
            except ValueError:
                await update.message.reply_text("⚠️ معرف المستخدم غير صحيح. الرجاء إرسال رقم صحيح.")
                return
        
        # بقية معالجة الرسائل
        if not check_vip_status(user_id):
            await update.message.reply_text("⛔ هذه الميزة متاحة لأعضاء VIP فقط\n\n"
                                          "استخدم /start لمعرفة كيفية الحصول على عضوية VIP")
            return
        
        if not await check_channel_subscription(user_id, context.bot):
            channels = get_required_channels()
            message = "❗ يجب تجديد الاشتراك في القنوات:\n"
            for channel in channels:
                message += f"- @{channel[1]}\n"
            await update.message.reply_text(message)
            return
        
        if context.user_data.get('awaiting_code'):
            try:
                response = model.generate_content(
                    f"أكتب كود برمجي فقط بدون شرح حسب الطلب التالي:\n\n{user_message}"
                )
                await update.message.reply_text(f"```python\n{response.text}\n```", 
                                              parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Error generating code: {e}")
                await update.message.reply_text(f"⚠️ حدث خطأ: {str(e)}")
            
            context.user_data.pop('awaiting_code', None)
            return
        
        await update.message.reply_text("🔍 اختر أحد الخيارات من القائمة:", 
                                      reply_markup=main_keyboard(user_id))
    
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text("⚠️ حدث خطأ غير متوقع. الرجاء المحاولة لاحقًا.")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        
        if query.data == 'write_code':
            if not check_vip_status(user_id):
                await query.edit_message_text("⛔ هذه الميزة تحتاج عضوية VIP\n\n"
                                            "استخدم /start لمعرفة كيفية الحصول عليها")
                return
            
            await query.edit_message_text("📝 أرسل وصف الكود الذي تريده مع ذكر اللغة:\nمثال: \"دالة بلغة Python لتحويل التاريخ\"")
            context.user_data['awaiting_code'] = True
        
        elif query.data == 'get_vip':
            await show_vip_options(query, user_id)
        
        elif query.data == 'admin_panel' and user_id == ADMIN_ID:
            await admin_panel(query)
        
        elif query.data == 'main_menu':
            await query.edit_message_text("🔍 اختر أحد الخيارات من القائمة:", 
                                       reply_markup=main_keyboard(user_id))
        
        # معالجة أزرار إهداء VIP
        elif query.data.startswith('gift_vip_'):
            gift_type = query.data.split('_')[2]
            context.user_data['gift_type'] = gift_type
            context.user_data['awaiting_user_id_for_gift'] = True
            await query.edit_message_text("📩 أرسل معرف المستخدم (user ID) الذي تريد إهداءه VIP:")
        
        elif query.data == 'gift_vip_menu':
            await gift_vip_menu(query)
    
    except Exception as e:
        logger.error(f"Error in button handler: {e}")
        await query.edit_message_text("⚠️ حدث خطأ غير متوقع. الرجاء المحاولة لاحقًا.")

async def gift_vip_menu(query):
    keyboard = [
        [InlineKeyboardButton("هدية VIP دائمة", callback_data='gift_vip_permanent')],
        [InlineKeyboardButton("هدية VIP لمدة 7 أيام", callback_data='gift_vip_7')],
        [InlineKeyboardButton("هدية VIP لمدة 30 يوم", callback_data='gift_vip_30')],
        [InlineKeyboardButton("هدية VIP لمدة 90 يوم", callback_data='gift_vip_90')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='admin_panel')]
    ]
    
    await query.edit_message_text("🎁 اختر نوع هدية VIP:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_panel(query):
    try:
        keyboard = [
            [InlineKeyboardButton("📊 الإحصائيات", callback_data='admin_stats')],
            [InlineKeyboardButton("📢 إرسال إشعار", callback_data='admin_broadcast')],
            [InlineKeyboardButton("🛠 إدارة القنوات", callback_data='manage_channels')],
            [InlineKeyboardButton("⚙️ تعديل الإعدادات", callback_data='edit_settings')],
            [InlineKeyboardButton("🎁 إهداء VIP", callback_data='gift_vip_menu')],  # إضافة زر جديد
            [InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')]
        ]
        
        await query.edit_message_text("🛠 لوحة تحكم المدير:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    except Exception as e:
        logger.error(f"Error in admin panel: {e}")
        await query.edit_message_text("⚠️ حدث خطأ في فتح لوحة التحكم. الرجاء المحاولة لاحقًا.")

async def show_vip_options(query, user_id):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND is_active = 1", (user_id,))
        ref_count = c.fetchone()[0]
        conn.close()
        
        required = get_setting('initial_invites_required')
        days = get_setting('free_trial_days')
        
        message = f"""
        🎟 نظام العضوية VIP:
        
        - عضوية مجانية {days} أيام عند دعوة {required} مستخدم
        - لديك {ref_count} من أصل {required} دعوة
        - رابط دعوتك: https://t.me/{BOT_USERNAME}?start=ref_{user_id}
        """
        
        keyboard = [
            [InlineKeyboardButton("🔗 مشاركة رابط الدعوة", switch_inline_query=f"انضم عبر رابط الدعوة هذا: https://t.me/{BOT_USERNAME}?start=ref_{user_id}")],
            [InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')]
        ]
        
        await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup(keyboard))
    
    except Exception as e:
        logger.error(f"Error showing VIP options: {e}")
        await query.edit_message_text("⚠️ حدث خطأ في عرض خيارات VIP. الرجاء المحاولة لاحقًا.")

# ========== التشغيل الرئيسي ==========
def main():
    try:
        logger.info("Starting bot...")
        
        # تهيئة التطبيق
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # إضافة المعالجات
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
        
        # إعداد ويب هوك للتشغيل على Render
        PORT = int(os.environ.get('PORT', 5000))
        
        # تأكد أن الرابط ينتهي بـ / إذا لم يكن كذلك
        webhook_url = WEBHOOK_URL
        if not webhook_url.endswith('/'):
            webhook_url += '/'
        
        # إعدادات ويب هوك
        logger.info(f"Setting webhook to: {webhook_url}{TELEGRAM_TOKEN}")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{webhook_url}{TELEGRAM_TOKEN}",
            cert=None,
            url_path=TELEGRAM_TOKEN,
            drop_pending_updates=True
        )
        
        logger.info("Bot started successfully with webhook")
    
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}")

if __name__ == "__main__":
    main()
