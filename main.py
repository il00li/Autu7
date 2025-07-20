import os
import sqlite3
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import google.generativeai as genai
from apscheduler.schedulers.background import BackgroundScheduler

# إعداد تسجيل الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تهيئة المجدول
scheduler = BackgroundScheduler()
scheduler.start()

# التوكنات والإعدادات (من متغيرات البيئة)
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8110119856:AAEKyEiIlpHP2e-xOQym0YHkGEBLRgyG_wA')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', 'AIzaSyAEULfP5zi5irv4yRhFugmdsjBoLk7kGsE')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '7251748706'))
BOT_USERNAME = os.environ.get('BOT_USERNAME', '@SEAK7_BOT')
ARCHIVE_CHANNEL = os.environ.get('ARCHIVE_CHANNEL', '@PoetryArchive')
WEBHOOK_URL = "https://autu7.onrender.com"  # رابط الويب هوك الخاص بك

# تهيئة Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-pro-latest')

# إعداد قاعدة البيانات
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'poetry_bot.sqlite')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # جدول المستخدمين
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, 
                 join_date TEXT, notification_time INTEGER DEFAULT 5)''')
    
    # جدول القنوات
    c.execute('''CREATE TABLE IF NOT EXISTS channels
                 (channel_id TEXT PRIMARY KEY, user_id INTEGER, 
                 title TEXT, added_date TEXT, is_active INTEGER DEFAULT 1,
                 attribution INTEGER DEFAULT 1)''')
    
    # جدول إعدادات المستخدم
    c.execute('''CREATE TABLE IF NOT EXISTS user_settings
                 (user_id INTEGER PRIMARY KEY, interval_hours INTEGER DEFAULT 24,
                 style TEXT DEFAULT 'classic')''')
    
    # جدول المنشورات
    c.execute('''CREATE TABLE IF NOT EXISTS posts
                 (post_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER, channel_id TEXT,
                  content TEXT, poet TEXT, book TEXT,
                  post_time TEXT, style TEXT)''')
    
    # جدول الجدول الزمني
    c.execute('''CREATE TABLE IF NOT EXISTS schedule
                 (user_id INTEGER PRIMARY KEY, next_post_time TEXT)''')
    
    conn.commit()
    conn.close()

init_db()
# ========== دوال المساعدة ==========
def register_user(user):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?)",
              (user.id, user.username, user.first_name, datetime.now().isoformat(), 5))
    
    # إضافة إعدادات افتراضية للمستخدم الجديد
    c.execute("INSERT OR IGNORE INTO user_settings VALUES (?, ?, ?)",
              (user.id, 24, 'classic'))
    conn.commit()
    conn.close()

def get_user_settings(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT interval_hours, style FROM user_settings WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result if result else (24, 'classic')

def update_user_settings(user_id, interval=None, style=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    if interval is not None:
        c.execute("UPDATE user_settings SET interval_hours = ? WHERE user_id = ?", (interval, user_id))
    
    if style is not None:
        c.execute("UPDATE user_settings SET style = ? WHERE user_id = ?", (style, user_id))
    
    conn.commit()
    conn.close()

def add_channel(user_id, channel_id, channel_title):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # التحقق من عدد القنوات المسموح بها (3 قنوات كحد أقصى)
    c.execute("SELECT COUNT(*) FROM channels WHERE user_id = ?", (user_id,))
    channel_count = c.fetchone()[0]
    
    if channel_count >= 3:
        conn.close()
        return False, "لقد وصلت للحد الأقصى (3 قنوات). الرجاء إزالة قناة أولاً."
    
    c.execute("INSERT OR REPLACE INTO channels VALUES (?, ?, ?, ?, ?, ?)",
              (channel_id, user_id, channel_title, datetime.now().isoformat(), 1, 1))
    conn.commit()
    conn.close()
    return True, "تمت إضافة القناة بنجاح!"

def remove_channel(user_id, channel_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM channels WHERE user_id = ? AND channel_id = ?", (user_id, channel_id))
    conn.commit()
    deleted = c.rowcount > 0
    conn.close()
    return deleted

def get_user_channels(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT channel_id, title, is_active, attribution FROM channels WHERE user_id = ?", (user_id,))
    channels = c.fetchall()
    conn.close()
    return channels

def toggle_channel_attribution(user_id, channel_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE channels SET attribution = NOT attribution WHERE user_id = ? AND channel_id = ?", 
              (user_id, channel_id))
    conn.commit()
    conn.close()

def schedule_next_post(user_id):
    interval, _ = get_user_settings(user_id)
    next_time = datetime.now() + timedelta(hours=interval)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO schedule VALUES (?, ?)", 
              (user_id, next_time.isoformat()))
    conn.commit()
    conn.close()
    return next_time

def get_scheduled_posts():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, next_post_time FROM schedule")
    posts = c.fetchall()
    conn.close()
    return posts

async def generate_poetry():
    try:
        prompt = """
        اكتب بيتين من الشعر العربي الأصيل في موضوع عشوائي. 
        يجب أن يكون البيتان متوافقين في الوزن والقافية.
        في نهاية الناتج، اذكر اسم شاعر وكتاب خياليين بالشكل التالي:
        
        الشاعر: [اسم الشاعر]
        من كتاب: [اسم الكتاب]
        """
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Error generating poetry: {e}")
        return None

def format_poetry(content, style, show_attribution=True):
    # استخراج الشاعر والكتاب من المحتوى
    poet = "شاعر غير معروف"
    book = "ديوان شعري"
    
    if "الشاعر:" in content and "من كتاب:" in content:
        parts = content.split("الشاعر:")
        if len(parts) > 1:
            poet_book = parts[1].split("من كتاب:")
            if len(poet_book) > 1:
                poet = poet_book[0].strip()
                book = poet_book[1].strip()
                content = parts[0].strip()
    
    # تطبيق الأنماط المختلفة
    if style == 'decorated':
        formatted = f"✨ {content} ✨"
    elif style == 'abbreviated':
        formatted = f"⚡ {content.split('.')[0]}"
    else:  # classic
        formatted = content
    
    # إضافة حقوق النشر إذا لزم الأمر
    if show_attribution:
        formatted += f"\n\nالشاعر: {poet}\nمن كتاب: {book}\n🔗 المصدر: {BOT_USERNAME}"
    
    return formatted, poet, book

async def send_notification(user_id, bot):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"⏰ سيتم النشر في قنواتك خلال 5 دقائق!\n"
                 f"يمكنك إلغاء النشر أو تعديل المحتوى باستخدام /cancel"
        )
    except Exception as e:
        logger.error(f"Error sending notification: {e}")

# ========== واجهة المستخدم ==========
def main_keyboard(user_id):
    keyboard = [
        [InlineKeyboardButton("➕ إضافة قناة", callback_data='add_channel')],
        [InlineKeyboardButton("🗑️ إدارة القنوات", callback_data='manage_channels')],
        [InlineKeyboardButton("⚙️ إعدادات النشر", callback_data='settings')],
        [InlineKeyboardButton("📜 توليد أبيات الآن", callback_data='generate_now')],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data='stats')],
        [InlineKeyboardButton("❓ المساعدة", callback_data='help')]
    ]
    
    return InlineKeyboardMarkup(keyboard)

def channel_management_keyboard(channels):
    keyboard = []
    for channel_id, title, _, _ in channels:
        keyboard.append([InlineKeyboardButton(f"📢 {title}", callback_data=f'channel_{channel_id}')])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')])
    return InlineKeyboardMarkup(keyboard)

def channel_options_keyboard(channel_id):
    keyboard = [
        [InlineKeyboardButton("تعطيل/تفعيل", callback_data=f'toggle_{channel_id}')],
        [InlineKeyboardButton("إخفاء/إظهار الحقوق", callback_data=f'attribution_{channel_id}')],
        [InlineKeyboardButton("❌ حذف القناة", callback_data=f'delete_{channel_id}')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='manage_channels')]
    ]
    return InlineKeyboardMarkup(keyboard)

def settings_keyboard():
    keyboard = [
        [InlineKeyboardButton("⏰ تغيير توقيت النشر", callback_data='change_interval')],
        [InlineKeyboardButton("🎨 تغيير نمط النشر", callback_data='change_style')],
        [InlineKeyboardButton("🔔 تعديل وقت التنبيه", callback_data='change_notification')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')]
    ]
    return InlineKeyboardMarkup(keyboard)

def interval_keyboard():
    keyboard = [
        [InlineKeyboardButton("كل 6 ساعات", callback_data='interval_6')],
        [InlineKeyboardButton("كل 12 ساعة", callback_data='interval_12')],
        [InlineKeyboardButton("كل 24 ساعة", callback_data='interval_24')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='settings')]
    ]
    return InlineKeyboardMarkup(keyboard)

def style_keyboard():
    keyboard = [
        [InlineKeyboardButton("كلاسيكي 🏺", callback_data='style_classic')],
        [InlineKeyboardButton("مزخرف ✨", callback_data='style_decorated')],
        [InlineKeyboardButton("مختصر ⚡", callback_data='style_abbreviated')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='settings')]
    ]
    return InlineKeyboardMarkup(keyboard)
    # ========== معالجة الأوامر والرسائل ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        register_user(user)
        
        welcome_msg = f"""
        🎉 أهلاً {user.first_name} في بوت الأبيات العربية!
        
        🤖 مهمتي توليد أبيات شعرية عربية أصيلة ونشرها في قنواتك تلقائياً.
        
        ✨ الميزات:
        - توليد أبيات شعرية فريدة
        - نشر تلقائي في قنواتك
        - تخصيص جدول النشر
        - أرشيف للقصائد المنشورة
        
        اختر من القائمة لبدء الإعداد:
        """
        await update.message.reply_text(welcome_msg, reply_markup=main_keyboard(user.id))
    
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ غير متوقع. الرجاء المحاولة لاحقًا.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    🆘 مركز المساعدة:
    
    🤖 هذا البوت مخصص لتوليد ونشر الأبيات الشعرية العربية في قنواتك التليجرام.
    
    ✨ الميزات:
    1. توليد أبيات شعرية عربية فريدة
    2. نشر تلقائي في قنواتك
    3. تخصيص جدول النشر (6, 12, 24 ساعة)
    4. أرشيف لجميع القصائد المنشورة
    
    ⚙️ كيفية الاستخدام:
    - استخدم /start لبدء الإعداد
    - أضف قنواتك عبر زر "إضافة قناة"
    - اضبط إعدادات النشر حسب رغبتك
    
    📞 للدعم الفني:
    تواصل مع المطور: @Ili8_8ill
    """
    await update.message.reply_text(help_text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # عدد القنوات
    c.execute("SELECT COUNT(*) FROM channels WHERE user_id = ?", (user_id,))
    channel_count = c.fetchone()[0]
    
    # عدد المنشورات
    c.execute("SELECT COUNT(*) FROM posts WHERE user_id = ?", (user_id,))
    post_count = c.fetchone()[0]
    
    # إعدادات المستخدم
    interval, style = get_user_settings(user_id)
    
    stats_text = f"""
    📊 إحصائيات حسابك:
    
    - عدد القنوات: {channel_count}/3
    - عدد المنشورات: {post_count}
    - تواتر النشر: كل {interval} ساعة
    - نمط النشر: {style}
    
    ⏳ موعد النشر التالي: {schedule_next_post(user_id).strftime('%Y-%m-%d %H:%M')}
    """
    await update.message.reply_text(stats_text)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    try:
        if query.data == 'main_menu':
            await query.edit_message_text("اختر من القائمة:", reply_markup=main_keyboard(user_id))
        
        elif query.data == 'add_channel':
            await query.edit_message_text(
                "📩 أرسل معرف القناة (يجب أن يكون البوت مشرفاً فيها):\n"
                "مثال: @channel_username",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')]])
            )
            context.user_data['awaiting_channel'] = True
        
        elif query.data == 'manage_channels':
            channels = get_user_channels(user_id)
            if not channels:
                await query.edit_message_text(
                    "⚠️ لم تقم بإضافة أي قنوات بعد.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')]])
                )
                return
            
            await query.edit_message_text(
                "📢 اختر قناة للإدارة:",
                reply_markup=channel_management_keyboard(channels)
            )
        
        elif query.data.startswith('channel_'):
            channel_id = query.data.split('_')[1]
            context.user_data['current_channel'] = channel_id
            channels = get_user_channels(user_id)
            title = next((ch[1] for ch in channels if ch[0] == channel_id), channel_id)
            
            await query.edit_message_text(
                f"⚙️ إدارة القناة: {title}",
                reply_markup=channel_options_keyboard(channel_id)
            )
        
        elif query.data.startswith('toggle_'):
            channel_id = query.data.split('_')[1]
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE channels SET is_active = NOT is_active WHERE channel_id = ?", (channel_id,))
            conn.commit()
            conn.close()
            
            channels = get_user_channels(user_id)
            title = next((ch[1] for ch in channels if ch[0] == channel_id), channel_id)
            status = "مفعّلة" if next((ch[2] for ch in channels if ch[0] == channel_id), 1) else "معطّلة"
            
            await query.edit_message_text(
                f"✅ تم تغيير حالة القناة {title} إلى: {status}",
                reply_markup=channel_options_keyboard(channel_id)
            )
        
        elif query.data.startswith('attribution_'):
            channel_id = query.data.split('_')[1]
            toggle_channel_attribution(user_id, channel_id)
            
            channels = get_user_channels(user_id)
            title = next((ch[1] for ch in channels if ch[0] == channel_id), channel_id)
            attribution = "مفعّل" if next((ch[3] for ch in channels if ch[0] == channel_id), 1) else "معطّل"
            
            await query.edit_message_text(
                f"✅ تم تغيير إظهار الحقوق للقناة {title} إلى: {attribution}",
                reply_markup=channel_options_keyboard(channel_id)
            )
        
        elif query.data.startswith('delete_'):
            channel_id = query.data.split('_')[1]
            success = remove_channel(user_id, channel_id)
            
            if success:
                await query.edit_message_text(
                    "✅ تم حذف القناة بنجاح",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='manage_channels')]])
                )
            else:
                await query.edit_message_text(
                    "⚠️ لم يتم العثور على القناة",
                    reply_markup=channel_options_keyboard(channel_id)
                )
        
        elif query.data == 'settings':
            await query.edit_message_text(
                "⚙️ إعدادات النشر:",
                reply_markup=settings_keyboard()
            )
        
        elif query.data == 'change_interval':
            await query.edit_message_text(
                "⏰ اختر تواتر النشر:",
                reply_markup=interval_keyboard()
            )
        
        elif query.data.startswith('interval_'):
            hours = int(query.data.split('_')[1])
            update_user_settings(user_id, interval=hours)
            schedule_next_post(user_id)
            
            await query.edit_message_text(
                f"✅ تم ضبط تواتر النشر على: كل {hours} ساعة",
                reply_markup=settings_keyboard()
            )
        
        elif query.data == 'change_style':
            await query.edit_message_text(
                "🎨 اختر نمط النشر:",
                reply_markup=style_keyboard()
            )
        
        elif query.data.startswith('style_'):
            style = query.data.split('_')[1]
            update_user_settings(user_id, style=style)
            
            await query.edit_message_text(
                f"✅ تم تغيير نمط النشر إلى: {style}",
                reply_markup=settings_keyboard()
            )
        
        elif query.data == 'generate_now':
            await query.edit_message_text("⏳ جاري توليد الأبيات...")
            poetry = await generate_poetry()
            
            if not poetry:
                await query.edit_message_text("⚠️ فشل في توليد الأبيات. الرجاء المحاولة لاحقاً.")
                return
            
            _, style = get_user_settings(user_id)
            formatted, poet, book = format_poetry(poetry, style, True)
            
            await query.edit_message_text(
                f"✅ تم توليد الأبيات:\n\n{formatted}\n\n"
                "هل تريد نشرها في قنواتك الآن؟",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ نعم، انشر الآن", callback_data='publish_now')],
                    [InlineKeyboardButton("❌ لا، تجاهل", callback_data='main_menu')]
                ])
            )
            context.user_data['generated_poetry'] = (poetry, poet, book)
        
        elif query.data == 'publish_now':
            poetry, poet, book = context.user_data['generated_poetry']
            channels = get_user_channels(user_id)
            _, style = get_user_settings(user_id)
            
            published = 0
            for channel_id, title, is_active, attribution in channels:
                if not is_active:
                    continue
                
                formatted, _, _ = format_poetry(poetry, style, attribution)
                
                try:
                    await context.bot.send_message(
                        chat_id=channel_id,
                        text=formatted
                    )
                    published += 1
                    
                    # تسجيل المنشور في قاعدة البيانات
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("INSERT INTO posts VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)",
                              (user_id, channel_id, poetry, poet, book, 
                               datetime.now().isoformat(), style))
                    conn.commit()
                    conn.close()
                    
                    # إرسال إلى قناة الأرشيف
                    try:
                        await context.bot.send_message(
                            chat_id=ARCHIVE_CHANNEL,
                            text=f"📜 {poetry}\n\nالشاعر: {poet}\nمن كتاب: {book}"
                        )
                    except:
                        pass
                    
                except Exception as e:
                    logger.error(f"Error publishing to {channel_id}: {e}")
            
            await query.edit_message_text(
                f"✅ تم النشر في {published} قناة بنجاح!",
                reply_markup=main_keyboard(user_id)
            )
            context.user_data.pop('generated_poetry', None)
        
        elif query.data == 'help':
            await help_command(update, context)
        
        elif query.data == 'stats':
            await stats_command(update, context)
    
    except Exception as e:
        logger.error(f"Error in button handler: {e}")
        await query.edit_message_text("⚠️ حدث خطأ غير متوقع. الرجاء المحاولة لاحقًا.")

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        user_message = update.message.text
        
        if context.user_data.get('awaiting_channel'):
            if user_message.startswith('@'):
                try:
                    # الحصول على معلومات القناة
                    chat = await context.bot.get_chat(user_message)
                    success, message = add_channel(user_id, chat.id, chat.title)
                    
                    if success:
                        await update.message.reply_text(
                            f"✅ {message}\nالقناة: {chat.title}",
                            reply_markup=main_keyboard(user_id)
                        )
                    else:
                        await update.message.reply_text(
                            f"⚠️ {message}",
                            reply_markup=main_keyboard(user_id)
                        )
                except Exception as e:
                    await update.message.reply_text(
                        "⚠️ تعذر العثور على القناة. تأكد من:\n"
                        "1. صحة معرف القناة\n"
                        "2. أن البوت مشرف في القناة\n",
                        reply_markup=main_keyboard(user_id)
                    )
                    logger.error(f"Error adding channel: {e}")
                
                context.user_data.pop('awaiting_channel', None)
                return
        
        await update.message.reply_text(
            "اختر من القائمة لاستخدام البوت:",
            reply_markup=main_keyboard(user_id)
        )
    
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text("⚠️ حدث خطأ غير متوقع. الرجاء المحاولة لاحقًا.")

# ========== وظائف المجدولة ==========
async def scheduled_posting():
    try:
        posts = get_scheduled_posts()
        now = datetime.now()
        
        for user_id, next_time_str in posts:
            next_time = datetime.fromisoformat(next_time_str)
            
            # التحقق إذا حان وقت النشر
            if now >= next_time:
                # إرسال تنبيه قبل 5 دقائق
                await send_notification(user_id, context.bot)
                await asyncio.sleep(300)  # انتظار 5 دقائق
                
                # توليد ونشر الأبيات
                poetry = await generate_poetry()
                if not poetry:
                    continue
                
                channels = get_user_channels(user_id)
                interval, style = get_user_settings(user_id)
                
                for channel_id, title, is_active, attribution in channels:
                    if not is_active:
                        continue
                    
                    formatted, poet, book = format_poetry(poetry, style, attribution)
                    
                    try:
                        await context.bot.send_message(
                            chat_id=channel_id,
                            text=formatted
                        )
                        
                        # تسجيل المنشور في قاعدة البيانات
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute("INSERT INTO posts VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)",
                                  (user_id, channel_id, poetry, poet, book, 
                                   datetime.now().isoformat(), style))
                        conn.commit()
                        conn.close()
                        
                        # إرسال إلى قناة الأرشيف
                        try:
                            await context.bot.send_message(
                                chat_id=ARCHIVE_CHANNEL,
                                text=f"📜 {poetry}\n\nالشاعر: {poet}\nمن كتاب: {book}"
                            )
                        except:
                            pass
                        
                    except Exception as e:
                        logger.error(f"Error publishing to {channel_id}: {e}")
                
                # جدولة النشر التالي
                schedule_next_post(user_id)
    
    except Exception as e:
        logger.error(f"Error in scheduled posting: {e}")

# إضافة المهمة المجدولة
scheduler.add_job(scheduled_posting, 'interval', minutes=5)

# ========== التشغيل الرئيسي ==========
def main():
    try:
        logger.info("Starting Poetry Bot...")
        
        # تهيئة التطبيق
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # إضافة المعالجات
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('help', help_command))
        application.add_handler(CommandHandler('stats', stats_command))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
        
        # إعداد ويب هوك للتشغيل على Render
        PORT = int(os.environ.get('PORT', 5000))
        webhook_url = WEBHOOK_URL.rstrip('/') + '/'
        
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
        
        logger.info("Poetry Bot started successfully")
    
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}")

if __name__ == "__main__":
    main()
