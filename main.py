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

# التوكنات والإعدادات (من متغيرات البيئة)
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
GEMINI_API_KEY = os.environ['GEMINI_API_KEY']
ADMIN_ID = int
# ========== دوال المساعدة ==========
def register_user(user):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?)",
              (user.id, user.username, user.first_name, datetime.now().isoformat(), 5))
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
    if interval: c.execute("UPDATE user_settings SET interval_hours = ? WHERE user_id = ?", (interval, user_id))
    if style: c.execute("UPDATE user_settings SET style = ? WHERE user_id = ?", (style, user_id))
    conn.commit()
    conn.close()

def add_channel(user_id, channel_id, channel_title):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM channels WHERE user_id = ?", (user_id,))
    if c.fetchone()[0] >= 3:
        return False, "لقد وصلت للحد الأقصى (3 قنوات)"
    
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
    c.execute("INSERT OR REPLACE INTO schedule VALUES (?, ?)", (user_id, next_time.isoformat()))
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
        response = model.generate_content(
            "اكتب بيتين من الشعر العربي الأصيل في موضوع عشوائي. "
            "يجب أن يكون البيتان متوافقين في الوزن والقافية. "
            "في النهاية اكتب: الشاعر: [اسم] من كتاب: [اسم]"
        )
        return response.text
    except Exception as e:
        logger.error(f"Error generating poetry: {e}")
        return None

def format_poetry(content, style, show_attribution=True):
    poet, book = "شاعر غير معروف", "ديوان شعري"
    if "الشاعر:" in content and "من كتاب:" in content:
        parts = content.split("الشاعر:")
        if len(parts) > 1:
            poet_book = parts[1].split("من كتاب:")
            if len(poet_book) > 1:
                poet, book = poet_book[0].strip(), poet_book[1].strip()
                content = parts[0].strip()
    
    if style == 'decorated': formatted = f"✨ {content} ✨"
    elif style == 'abbreviated': formatted = f"⚡ {content.split('.')[0]}"
    else: formatted = content
    
    if show_attribution:
        formatted += f"\n\nالشاعر: {poet}\nمن كتاب: {book}\n🔗 المصدر: {BOT_USERNAME}"
    
    return formatted, poet, book
    # ========== واجهة المستخدم ==========
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة قناة", callback_data='add_channel')],
        [InlineKeyboardButton("🗑️ إدارة القنوات", callback_data='manage_channels')],
        [InlineKeyboardButton("⚙️ إعدادات النشر", callback_data='settings')],
        [InlineKeyboardButton("📜 توليد أبيات الآن", callback_data='generate_now')],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data='stats')],
        [InlineKeyboardButton("❓ المساعدة", callback_data='help')]
    ])

def back_to_main():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')]])

def channel_management_keyboard(channels):
    keyboard = [[InlineKeyboardButton(f"📢 {title}", callback_data=f'channel_{id}')] for id, title, _, _ in channels]
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')])
    return InlineKeyboardMarkup(keyboard)

def channel_options_keyboard(channel_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("تعطيل/تفعيل", callback_data=f'toggle_{channel_id}')],
        [InlineKeyboardButton("إخفاء/إظهار الحقوق", callback_data=f'attribution_{channel_id}')],
        [InlineKeyboardButton("❌ حذف القناة", callback_data=f'delete_{channel_id}')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='manage_channels')]
    ])

def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏰ تغيير توقيت النشر", callback_data='change_interval')],
        [InlineKeyboardButton("🎨 تغيير نمط النشر", callback_data='change_style')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='main_menu')]
    ])

def interval_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("كل 6 ساعات", callback_data='interval_6')],
        [InlineKeyboardButton("كل 12 ساعة", callback_data='interval_12')],
        [InlineKeyboardButton("كل 24 ساعة", callback_data='interval_24')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='settings')]
    ])

def style_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("كلاسيكي 🏺", callback_data='style_classic')],
        [InlineKeyboardButton("مزخرف ✨", callback_data='style_decorated')],
        [InlineKeyboardButton("مختصر ⚡", callback_data='style_abbreviated')],
        [InlineKeyboardButton("🔙 رجوع", callback_data='settings')]
    ])
    # ========== معالجة الأوامر والرسائل ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    await update.message.reply_text(
        f"🎉 أهلاً {user.first_name} في بوت الأبيات العربية!\n\n"
        "🤖 مهمتي توليد أبيات شعرية عربية ونشرها في قنواتك\n"
        "اختر من القائمة لبدء الإعداد:",
        reply_markup=main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 مركز المساعدة:\n\n"
        "1. أضف قنواتك (حد أقصى 3 قنوات)\n"
        "2. اضبط إعدادات النشر والتوقيت\n"
        "3. استخدم 'توليد أبيات الآن' للإنشاء الفوري\n"
        "📞 الدعم: @Ili8_8ill"
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM channels WHERE user_id = ?", (user_id,))
    channel_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM posts WHERE user_id = ?", (user_id,))
    post_count = c.fetchone()[0]
    interval, style = get_user_settings(user_id)
    
    await update.message.reply_text(
        f"📊 الإحصائيات:\n\n"
        f"- القنوات: {channel_count}/3\n"
        f"- المنشورات: {post_count}\n"
        f"- التواتر: كل {interval} ساعة\n"
        f"- النمط: {style}"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    
    try:
        if data == 'main_menu':
            await query.edit_message_text("اختر من القائمة:", reply_markup=main_keyboard())
        
        elif data == 'add_channel':
            await query.edit_message_text("📩 أرسل معرف القناة (مثال: @channel):", reply_markup=back_to_main())
            context.user_data['awaiting_channel'] = True
        
        elif data == 'manage_channels':
            channels = get_user_channels(user_id)
            if not channels:
                await query.edit_message_text("⚠️ لم تقم بإضافة قنوات بعد", reply_markup=back_to_main())
            else:
                await query.edit_message_text("📢 اختر قناة:", reply_markup=channel_management_keyboard(channels))
        
        elif data.startswith('channel_'):
            channel_id = data.split('_')[1]
            context.user_data['current_channel'] = channel_id
            channels = get_user_channels(user_id)
            title = next((t for id, t, _, _ in channels if id == channel_id), "القناة")
            await query.edit_message_text(f"⚙️ إدارة: {title}", reply_markup=channel_options_keyboard(channel_id))
        
        elif data.startswith('toggle_'):
            channel_id = data.split('_')[1]
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE channels SET is_active = NOT is_active WHERE channel_id = ?", (channel_id,))
            conn.commit()
            conn.close()
            await query.edit_message_text("✅ تم تغيير حالة القناة", reply_markup=channel_options_keyboard(channel_id))
        
        elif data.startswith('attribution_'):
            channel_id = data.split('_')[1]
            toggle_channel_attribution(user_id, channel_id)
            await query.edit_message_text("✅ تم تغيير إظهار الحقوق", reply_markup=channel_options_keyboard(channel_id))
        
        elif data.startswith('delete_'):
            channel_id = data.split('_')[1]
            if remove_channel(user_id, channel_id):
                await query.edit_message_text("✅ تم حذف القناة", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data='manage_channels')]]))
        
        elif data == 'settings':
            await query.edit_message_text("⚙️ إعدادات النشر:", reply_markup=settings_keyboard())
        
        elif data == 'change_interval':
            await query.edit_message_text("⏰ اختر التواتر:", reply_markup=interval_keyboard())
        
        elif data.startswith('interval_'):
            hours = int(data.split('_')[1])
            update_user_settings(user_id, interval=hours)
            schedule_next_post(user_id)
            await query.edit_message_text(f"✅ تم الضبط: كل {hours} ساعة", reply_markup=settings_keyboard())
        
        elif data == 'change_style':
            await query.edit_message_text("🎨 اختر النمط:", reply_markup=style_keyboard())
        
        elif data.startswith('style_'):
            style = data.split('_')[1]
            update_user_settings(user_id, style=style)
            await query.edit_message_text(f"✅ تم التغيير: {style}", reply_markup=settings_keyboard())
        
        elif data == 'generate_now':
            await query.edit_message_text("⏳ جاري التوليد...")
            poetry = await generate_poetry()
            
            if poetry:
                _, style = get_user_settings(user_id)
                formatted, poet, book = format_poetry(poetry, style, True)
                context.user_data['generated_poetry'] = (poetry, poet, book)
                await query.edit_message_text(
                    f"✅ الناتج:\n\n{formatted}\n\nنشر الآن؟",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ نعم", callback_data='publish_now')],
                        [InlineKeyboardButton("❌ لا", callback_data='main_menu')]
                    ])
                )
            else:
                await query.edit_message_text("⚠️ فشل التوليد، حاول لاحقاً", reply_markup=main_keyboard())
        
        elif data == 'publish_now':
            if 'generated_poetry' not in context.user_data:
                await query.edit_message_text("⚠️ لم يتم توليد أبيات", reply_markup=main_keyboard())
                return
                
            poetry, poet, book = context.user_data['generated_poetry']
            channels = get_user_channels(user_id)
            _, style = get_user_settings(user_id)
            published = 0
            
            for channel_id, title, is_active, attribution in channels:
                if not is_active: continue
                
                formatted, _, _ = format_poetry(poetry, style, attribution)
                try:
                    await context.bot.send_message(chat_id=channel_id, text=formatted)
                    published += 1
                    
                    # تسجيل في قاعدة البيانات
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("INSERT INTO posts VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)",
                              (user_id, channel_id, poetry, poet, book, datetime.now().isoformat(), style))
                    conn.commit()
                    conn.close()
                    
                    # الأرشيف
                    try:
                        await context.bot.send_message(
                            chat_id=ARCHIVE_CHANNEL,
                            text=f"📜 {poetry}\n\nالشاعر: {poet}\nمن كتاب: {book}"
                        )
                    except:
                        pass
                    
                except Exception as e:
                    logger.error(f"Error publishing: {e}")
            
            await query.edit_message_text(f"✅ تم النشر في {published} قناة", reply_markup=main_keyboard())
            del context.user_data['generated_poetry']
        
        elif data == 'help':
            await help_command(update, context)
        
        elif data == 'stats':
            await stats_command(update, context)
            
    except Exception as e:
        logger.error(f"Button error: {e}")
        await query.edit_message_text("⚠️ حدث خطأ، يرجى المحاولة مرة أخرى", reply_markup=main_keyboard())

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    if context.user_data.get('awaiting_channel') and text.startswith('@'):
        try:
            chat = await context.bot.get_chat(text)
            success, message = add_channel(user_id, chat.id, chat.title)
            await update.message.reply_text(f"✅ {message}" if success else f"⚠️ {message}", reply_markup=main_keyboard())
        except Exception as e:
            await update.message.reply_text("⚠️ تعذر إضافة القناة، تأكد من:\n- صحة المعرف\n- أن البوت مشرف", reply_markup=main_keyboard())
            logger.error(f"Channel error: {e}")
        finally:
            context.user_data.pop('awaiting_channel', None)
    else:
        await update.message.reply_text("اختر من القائمة:", reply_markup=main_keyboard())
        # ========== وظائف المجدولة ==========
async def scheduled_posting_job():
    logger.info("Running scheduled posting...")
    posts = get_scheduled_posts()
    now = datetime.now()
    
    for user_id, next_time_str in posts:
        next_time = datetime.fromisoformat(next_time_str)
        if now >= next_time:
            try:
                # توليد الشعر
                poetry = await generate_poetry()
                if not poetry:
                    continue
                
                # الحصول على إعدادات المستخدم
                channels = get_user_channels(user_id)
                interval, style = get_user_settings(user_id)
                
                # النشر في القنوات
                for channel_id, _, is_active, attribution in channels:
                    if not is_active: continue
                    
                    formatted, poet, book = format_poetry(poetry, style, attribution)
                    try:
                        # الحصول على كائن البوت من السياق
                        bot = context.bot if 'context' in globals() else None
                        if not bot:
                            from telegram import Bot
                            bot = Bot(token=TELEGRAM_TOKEN)
                        
                        await bot.send_message(chat_id=channel_id, text=formatted)
                        
                        # تسجيل في قاعدة البيانات
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute("INSERT INTO posts VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)",
                                  (user_id, channel_id, poetry, poet, book, datetime.now().isoformat(), style))
                        conn.commit()
                        conn.close()
                        
                        # الأرشيف
                        try:
                            await bot.send_message(
                                chat_id=ARCHIVE_CHANNEL,
                                text=f"📜 {poetry}\n\nالشاعر: {poet}\nمن كتاب: {book}"
                            )
                        except Exception as e:
                            logger.error(f"Archive error: {e}")
                            
                    except Exception as e:
                        logger.error(f"Publishing error: {e}")
                
                # جدولة التالية
                schedule_next_post(user_id)
                
            except Exception as e:
                logger.error(f"Scheduled job error: {e}")

# ========== التشغيل الرئيسي ==========
def main():
    try:
        # بدء المجدول
        scheduler.add_job(scheduled_posting_job, 'interval', minutes=5)
        scheduler.start()
        logger.info("Scheduler started")
        
        # تهيئة تطبيق التليجرام
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        # تسجيل المعالجات
        application.add_handler(CommandHandler('start', start))
        application.add_handler(CommandHandler('help', help_command))
        application.add_handler(CommandHandler('stats', stats_command))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
        
        # إعداد ويب هوك
        PORT = int(os.environ.get('PORT', 5000))
        webhook_url = WEBHOOK_URL.rstrip('/') + '/'
        
        logger.info("Starting webhook...")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{webhook_url}{TELEGRAM_TOKEN}",
            url_path=TELEGRAM_TOKEN,
            drop_pending_updates=True
        )
        logger.info("Bot started successfully")
        
    except Exception as e:
        logger.critical(f"Fatal error: {e}")

if __name__ == "__main__":
    main()
