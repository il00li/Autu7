import os
import logging
import asyncio
import re
from telethon import TelegramClient, events
from telethon.tl.types import InputPhoneContact
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PhoneNumberInvalidError
from telethon.tl.functions.auth import SendCodeRequest, SignInRequest
import vobject
import json
from datetime import datetime

# إعداد التسجيل
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# بيانات API
API_ID = 23656977
API_HASH = '49d3f43531a92b3f5bc403766313ca1e'
BOT_TOKEN = '8398354970:AAFuQnkPoSCY-pVzrTpaIZ_BqL94t_yJ9Vo'

# إنشاء عميل Telethon للبوت
bot = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# حالات المستخدمين وإعداداتهم
user_data = {}

# دالة لتحميل بيانات المستخدمين
def load_user_data():
    global user_data
    try:
        if os.path.exists('user_data.json'):
            with open('user_data.json', 'r') as f:
                user_data = json.load(f)
    except Exception as e:
        logger.error(f"Error loading user data: {e}")
        user_data = {}

# دالة لحفظ بيانات المستخدمين
def save_user_data():
    try:
        with open('user_data.json', 'w') as f:
            json.dump(user_data, f)
    except Exception as e:
        logger.error(f"Error saving user data: {e}")

# تحميل بيانات المستخدمين عند البدء
load_user_data()

# تهيئة بيانات المستخدم إذا لم تكن موجودة
def init_user_data(user_id):
    if user_id not in user_data:
        user_data[user_id] = {
            'state': 'idle',
            'delay': 5,
            'channel': None,
            'vcf_file': None,
            'is_authenticated': False,
            'phone_numbers': [],
            'phone': None,
            'phone_code_hash': None,
            'client': None
        }
        save_user_data()

# إنشاء لوحة المفاتيح الرئيسية
def create_main_keyboard():
    from telethon.tl.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardHide
    
    buttons = [
        [KeyboardButton('📤 رفع ملف VCF'), KeyboardButton('⚙️ الإعدادات')],
        [KeyboardButton('❓ المساعدة'), KeyboardButton('📊 حالة البوت')],
        [KeyboardButton('🔑 تسجيل الدخول')]
    ]
    return ReplyKeyboardMarkup(buttons, resize=True)

# معالجة أمر /start
@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_id = event.sender_id
    init_user_data(user_id)
    
    welcome_text = """
    👋 مرحباً بك في بوت معالجة جهات الاتصال!
    
    📝 الميزات:
    - استقبال ملفات جهات الاتصال بصيغة .vcf
    - استخراج الأرقام والتحقق من وجودها على Telegram
    - إضافة المستخدمين إلى قناة محددة
    - قابلية ضبط وقت الانتظار بين كل إضافة
    - تسجيل الدخول برقم الهاتف وكود التحقق
    
    🔧 لبدء الاستخدام:
    1. اضغط على زر 'تسجيل الدخول' لإدخال رقم هاتفك
    2. اضغط على زر 'رفع ملف VCF'
    3. أرسل ملف جهات الاتصال
    4. حدد قناة الهدف
    5. اضبط الإعدادات
    6. ابدأ المعالجة
    
    ⚠️ ملاحظة: تأكد من أن الحساب لديه صلاحية إضافة أعضاء في القناة المستهدفة.
    """
    
    await event.reply(welcome_text, buttons=create_main_keyboard())

# معالجة الرسائل النصية
@bot.on(events.NewMessage)
async def message_handler(event):
    user_id = event.sender_id
    init_user_data(user_id)
    text = event.text.strip()
    
    if text == '📤 رفع ملف VCF':
        if not user_data[user_id].get('is_authenticated', False):
            await event.reply("⚠️ يجب عليك أولاً تسجيل الدخول من خلال زر 'تسجيل الدخول'")
            return
        await ask_for_vcf(event)
    
    elif text == '⚙️ الإعدادات':
        await show_settings(event)
    
    elif text == '❓ المساعدة':
        await send_help(event)
    
    elif text == '📊 حالة البوت':
        await show_status(event)
    
    elif text == '🔑 تسجيل الدخول':
        await ask_for_phone(event)
    
    elif user_data[user_id]['state'] == 'waiting_for_phone':
        await process_phone(event, text)
    
    elif user_data[user_id]['state'] == 'waiting_for_phone_code':
        await process_phone_code(event, text)
    
    elif user_data[user_id]['state'] == 'waiting_for_password':
        await process_password(event, text)
    
    elif user_data[user_id]['state'] == 'waiting_for_channel':
        await process_channel(event, text)
    
    elif user_data[user_id]['state'] == 'waiting_for_delay':
        await process_delay(event, text)

async def ask_for_phone(event):
    user_id = event.sender_id
    user_data[user_id]['state'] = 'waiting_for_phone'
    save_user_data()
    
    await event.reply("📱 الرجاء إرسال رقم هاتفك مع رمز الدولة (مثال: +1234567890):")

async def ask_for_vcf(event):
    user_id = event.sender_id
    user_data[user_id]['state'] = 'waiting_for_vcf'
    save_user_data()
    
    await event.reply("📁 يرجى إرسال ملف جهات الاتصال بصيغة .vcf")

async def show_settings(event):
    user_id = event.sender_id
    delay = user_data[user_id]['delay']
    channel = user_data[user_id]['channel'] or "غير محدد"
    session_status = "✅ مفعلة" if user_data[user_id].get('is_authenticated', False) else "❌ غير مفعلة"
    
    settings_text = f"""
    ⚙️ الإعدادات الحالية:
    
    ⏱ تأخير بين الإضافات: {delay} ثانية
    📢 القناة المستهدفة: {channel}
    🔑 حالة التسجيل: {session_status}
    
    الرجاء إرسال:
    - 'تأخير X' لتعيين وقت الانتظار بين الإضافات (X بالثواني)
    - 'قناة @username' لتعيين القناة المستهدفة
    - 'تسجيل' لتسجيل الدخول
    """
    
    await event.reply(settings_text)

async def send_help(event):
    help_text = """
    ❓ دليل استخدام البوت:
    
    1. انتقل إلى "تسجيل الدخول" وأدخل رقم هاتفك
    2. أدخل كود التحقق الذي ستستلمه على Telegram
    3. ارفع ملف VCF باستخدام الزر المخصص
    4. بعد معالجة الملف، سيطلب منك إدخال معرف القناة المستهدفة
    5. يمكنك ضبط وقت الانتظار بين كل إضافة من خلال إرسال 'تأخير X' (X بالثواني)
    6. ابدأ عملية الإضافة إلى القناة
    
    ⚠️ ملاحظات هامة:
    - تأكد من أن الحساب لديه صلاحية إضافة أعضاء إلى القنوات
    - الملف يجب أن يكون بصيغة .vcf صالحة
    - العملية قد تستغرق بعض الوقت حسب عدد الجهات
    - وقت الانتظار بين الإضافات يساعد على تجنب حظر الحساب
    
    📁 صيغة الملف: يجب أن يكون ملف VCF (vCard) يحتوي على أرقام الهواتف
    """
    
    await event.reply(help_text)

async def show_status(event):
    user_id = event.sender_id
    delay = user_data[user_id]['delay']
    channel = user_data[user_id]['channel'] or "غير محدد"
    state = user_data[user_id]['state']
    session_status = "✅ مفعلة" if user_data[user_id].get('is_authenticated', False) else "❌ غير مفعلة"
    
    status_text = f"""
    📊 حالة البوت:
    
    الحالة الحالية: {state}
    وقت الانتظار: {delay} ثانية
    القناة المستهدفة: {channel}
    حالة التسجيل: {session_status}
    
    البوت يعمل بشكل طبيعي ✅
    """
    
    await event.reply(status_text)

async def process_phone(event, text):
    user_id = event.sender_id
    
    if re.match(r'^\+[1-9]\d{1,14}$', text):
        user_data[user_id]['phone'] = text
        user_data[user_id]['state'] = 'waiting_for_phone_code'
        save_user_data()
        
        # إرسال طلب كود التحقق
        await send_phone_code(event, text)
    else:
        await event.reply("❌ رقم الهاتف غير صالح. يرجى إدخال رقم مع رمز الدولة (مثال: +1234567890)")

async def send_phone_code(event, phone):
    user_id = event.sender_id
    
    try:
        # إنشاء عميل جديد للمستخدم
        client = TelegramClient(f'sessions/user_{user_id}', API_ID, API_HASH)
        await client.connect()
        
        # إرسال طلب كود التحقق
        sent_code = await client.send_code_request(phone)
        user_data[user_id]['phone_code_hash'] = sent_code.phone_code_hash
        user_data[user_id]['client'] = client
        save_user_data()
        
        await event.reply("📲 تم إرسال كود التحقق إلى حسابك على Telegram. يرجى إدخال الكود المكون من 5 أرقام:")
        
    except FloodWaitError as e:
        await event.reply(f"⏳ يجب الانتظار {e.seconds} ثانية قبل المحاولة مرة أخرى.")
        user_data[user_id]['state'] = 'idle'
        save_user_data()
    except PhoneNumberInvalidError:
        await event.reply("❌ رقم الهاتف غير صالح. يرجى المحاولة مرة أخرى.")
        user_data[user_id]['state'] = 'idle'
        save_user_data()
    except Exception as e:
        logger.error(f"Error sending phone code: {e}")
        await event.reply(f"❌ حدث خطأ أثناء إرسال كود التحقق: {str(e)}")
        user_data[user_id]['state'] = 'idle'
        save_user_data()

async def process_phone_code(event, text):
    user_id = event.sender_id
    
    if re.match(r'^\d{5}$', text):
        user_data[user_id]['state'] = 'verifying_code'
        save_user_data()
        
        await verify_phone_code(event, text)
    else:
        await event.reply("❌ كود التحقق يجب أن يكون 5 أرقام. يرجى المحاولة مرة أخرى.")

async def verify_phone_code(event, code):
    user_id = event.sender_id
    
    try:
        client = user_data[user_id]['client']
        phone = user_data[user_id]['phone']
        phone_code_hash = user_data[user_id]['phone_code_hash']
        
        # تسجيل الدخول بالكود
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        
        # التحقق من نجاح التسجيل
        me = await client.get_me()
        user_data[user_id]['is_authenticated'] = True
        user_data[user_id]['state'] = 'idle'
        save_user_data()
        
        await event.reply(f"✅ تم تسجيل الدخول بنجاح!\n\n👤 الحساب: {me.first_name} {me.last_name or ''}\n📞 الهاتف: {me.phone}")
        
    except SessionPasswordNeededError:
        user_data[user_id]['state'] = 'waiting_for_password'
        save_user_data()
        await event.reply("🔒 حسابك محمي بكلمة مرور ثنائية. يرجى إدخال كلمة المرور:")
    except Exception as e:
        logger.error(f"Error verifying phone code: {e}")
        await event.reply(f"❌ فشل التحقق من الكود: {str(e)}")
        user_data[user_id]['state'] = 'idle'
        save_user_data()

async def process_password(event, text):
    user_id = event.sender_id
    
    if text:
        await verify_password(event, text)
    else:
        await event.reply("❌ يرجى إدخال كلمة المرور.")

async def verify_password(event, password):
    user_id = event.sender_id
    
    try:
        client = user_data[user_id]['client']
        
        # التحقق من كلمة المرور
        await client.sign_in(password=password)
        
        # التحقق من نجاح التسجيل
        me = await client.get_me()
        user_data[user_id]['is_authenticated'] = True
        user_data[user_id]['state'] = 'idle'
        save_user_data()
        
        await event.reply(f"✅ تم تسجيل الدخول بنجاح!\n\n👤 الحساب: {me.first_name} {me.last_name or ''}\n📞 الهاتف: {me.phone}")
        
    except Exception as e:
        logger.error(f"Error verifying password: {e}")
        await event.reply(f"❌ فشل التحقق من كلمة المرور: {str(e)}")
        user_data[user_id]['state'] = 'idle'
        save_user_data()

async def process_channel(event, text):
    user_id = event.sender_id
    
    if text.startswith('@'):
        user_data[user_id]['channel'] = text
        user_data[user_id]['state'] = 'idle'
        save_user_data()
        
        await event.reply(f"✅ تم تعيين القناة المستهدفة: {text}\n\nيمكنك الآن بدء الإضافة بإرسال 'بدء'")
    else:
        await event.reply("يجب أن يبدأ معرف القناة ب @. يرجى المحاولة مرة أخرى.")

async def process_delay(event, text):
    user_id = event.sender_id
    
    try:
        delay = int(text)
        if delay < 1 or delay > 60:
            await event.reply("وقت الانتظار يجب أن يكون بين 1 و 60 ثانية.")
        else:
            user_data[user_id]['delay'] = delay
            user_data[user_id]['state'] = 'idle'
            save_user_data()
            await event.reply(f"✅ تم تعيين وقت الانتظار إلى {delay} ثانية")
    except ValueError:
        await event.reply("يرجى إدخال رقم صحيح للوقت.")

# معالجة ملفات VCF
@bot.on(events.NewMessage(func=lambda e: e.document))
async def handle_document(event):
    user_id = event.sender_id
    init_user_data(user_id)
    
    # التحقق من حالة المستخدم
    if user_data[user_id]['state'] != 'waiting_for_vcf':
        return
    
    # التحقق من صيغة الملف
    file_name = event.document.attributes[0].file_name
    if not file_name.lower().endswith('.vcf'):
        await event.reply("الملف يجب أن يكون بصيغة .vcf. يرجى إرسال ملف صحيح.")
        return
    
    try:
        # تنزيل الملف
        file_path = await event.download_media(file=f"temp_{user_id}.vcf")
        
        user_data[user_id]['vcf_file'] = file_path
        user_data[user_id]['state'] = 'processing_vcf'
        save_user_data()
        
        # معالجة الملف
        await process_vcf_file(event, file_path)
        
    except Exception as e:
        logger.error(f"Error handling document: {e}")
        await event.reply("حدث خطأ أثناء معالجة الملف. يرجى المحاولة مرة أخرى.")

async def process_vcf_file(event, file_path):
    user_id = event.sender_id
    
    try:
        # قراءة ملف VCF
        with open(file_path, 'r', encoding='utf-8') as f:
            vcf_content = f.read()
        
        # تحليل محتوى VCF
        vcards = vobject.readComponents(vcf_content)
        phone_numbers = []
        
        for vcard in vcards:
            if hasattr(vcard, 'tel'):
                for tel in vcard.contents.get('tel', []):
                    if tel.value:
                        # تنظيف رقم الهاتف
                        phone = ''.join(filter(str.isdigit, tel.value))
                        if phone and len(phone) >= 8:  # تأكد أن الرقم صالح
                            phone_numbers.append(phone)
        
        if not phone_numbers:
            await event.reply("لم يتم العثور على أرقام هاتف صالحة في الملف.")
            return
        
        # حفظ الأرقام للمستخدم
        user_data[user_id]['phone_numbers'] = phone_numbers
        user_data[user_id]['state'] = 'waiting_for_channel'
        save_user_data()
        
        await event.reply(f"✅ تم استخراج {len(phone_numbers)} رقم من الملف.\n\nالرجاء إرسال معرف القناة المستهدفة (مثال: @channel_username):")
    
    except Exception as e:
        logger.error(f"Error processing VCF file: {e}")
        await event.reply("حدث خطأ أثناء معالجة الملف. يرجى التأكد من أن الملف صحيح.")
        
        # تنظيف الملف المؤقت
        if os.path.exists(file_path):
            os.remove(file_path)
        user_data[user_id]['vcf_file'] = None
        user_data[user_id]['state'] = 'idle'
        save_user_data()

# معالجة أمر البدء
@bot.on(events.NewMessage(pattern='^بدء$'))
async def start_adding_handler(event):
    user_id = event.sender_id
    init_user_data(user_id)
    
    if not user_data[user_id].get('phone_numbers'):
        await event.reply("لا توجد أرقام للمعالجة. يرجى رفع ملف VCF أولاً.")
        return
    
    if not user_data[user_id].get('channel'):
        await event.reply("لم يتم تحديد قناة مستهدفة. يرجى تعيين القناة أولاً.")
        return
    
    if not user_data[user_id].get('is_authenticated', False):
        await event.reply("⚠️ يجب عليك أولاً تسجيل الدخول من خلال زر 'تسجيل الدخول'")
        return
    
    # بدء عملية الإضافة
    await add_members_to_channel(event)

async def add_members_to_channel(event):
    user_id = event.sender_id
    
    try:
        phone_numbers = user_data[user_id]['phone_numbers']
        channel = user_data[user_id]['channel']
        delay = user_data[user_id]['delay']
        
        # إرسال رسالة التقدم
        progress_msg = await event.reply(f"جاري البدء بإضافة {len(phone_numbers)} عضو إلى {channel}...")
        
        added_count = 0
        skipped_count = 0
        error_count = 0
        
        # استخدام جلسة المستخدم
        client = user_data[user_id]['client']
        
        for i, phone in enumerate(phone_numbers):
            try:
                # تحديث رسالة التقدم
                if i % 10 == 0 or i == len(phone_numbers) - 1:
                    await progress_msg.edit(f"جاري المعالجة: {i+1}/{len(phone_numbers)}\nتمت الإضافة: {added_count}\nتم التخطي: {skipped_count}\nأخطاء: {error_count}")
                
                # التحقق من وجود الرقم على Telegram
                contact = InputPhoneContact(client_id=0, phone=phone, first_name="", last_name="")
                result = await client(ImportContactsRequest([contact]))
                
                if result.users:
                    # إضافة المستخدم إلى القناة
                    try:
                        await client.add_chat_members(channel, [result.users[0].id])
                        added_count += 1
                        logger.info(f"Added user {result.users[0].id} to channel {channel}")
                    except FloodWaitError as e:
                        # في حالة FloodWait، ننتظر المدة المطلوبة
                        wait_time = e.seconds
                        await progress_msg.edit(f"تم الوصول إلى حد الإضافات. جاري الانتظار {wait_time} ثانية...")
                        await asyncio.sleep(wait_time)
                        # إعادة المحاولة بعد الانتظار
                        await client.add_chat_members(channel, [result.users[0].id])
                        added_count += 1
                    except Exception as e:
                        logger.error(f"Error adding user to channel: {e}")
                        error_count += 1
                else:
                    skipped_count += 1
                
                # الانتظار بين الإضافات
                if delay > 0 and i < len(phone_numbers) - 1:
                    await asyncio.sleep(delay)
                    
            except Exception as e:
                logger.error(f"Error processing number {phone}: {e}")
                error_count += 1
                continue
        
        # إرسال النتيجة النهائية
        result_text = f"""
        ✅ تم الانتهاء من المعالجة!
        
        📊 النتائج:
        - العدد الإجمالي: {len(phone_numbers)}
        - تمت الإضافة: {added_count}
        - تم التخطي: {skipped_count}
        - أخطاء: {error_count}
        
        يمكنك رفع ملف جديد أو تعديل الإعدادات.
        """
        
        await progress_msg.edit(result_text)
    
    except Exception as e:
        logger.error(f"Error in add_members_to_channel: {e}")
        await event.reply(f"حدث خطأ أثناء عملية الإضافة: {str(e)}")

# إنشاء مجلد الجلسات إذا لم يكن موجوداً
if not os.path.exists('sessions'):
    os.makedirs('sessions')

# تشغيل البوت
if __name__ == '__main__':
    print("جارٍ تشغيل البوت...")
    bot.run_until_disconnected() 
