import os
import logging
import asyncio
import re
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.tl.types import InputPhoneContact
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PhoneNumberInvalidError
from telethon.tl.functions.auth import SendCodeRequest, SignInRequest
import vobject
import time

# إعداد التسجيل
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# بيانات API
API_ID = 23656977
API_HASH = '49d3f43531a92b3f5bc403766313ca1e'
BOT_TOKEN = '8398354970:AAFuQnkPoSCY-pVzrTpaIZ_BqL94t_yJ9Vo'

# إنشاء كائن البوت
bot = telebot.TeleBot(BOT_TOKEN)

# حالات المستخدمين وإعداداتهم
user_data = {}
phone_code_requests = {}

# زر Inline Keyboard الرئيسي
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    user_id = message.chat.id
    if user_id not in user_data:
        user_data[user_id] = {
            'state': 'idle',
            'delay': 5,
            'channel': None,
            'vcf_file': None,
            'client': None,
            'is_authenticated': False,
            'phone_numbers': [],
            'phone': None,
            'phone_code_hash': None
        }
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_upload = types.InlineKeyboardButton('📤 رفع ملف VCF', callback_data='upload_vcf')
    btn_settings = types.InlineKeyboardButton('⚙️ الإعدادات', callback_data='settings')
    btn_help = types.InlineKeyboardButton('❓ المساعدة', callback_data='help')
    btn_status = types.InlineKeyboardButton('📊 حالة البوت', callback_data='status')
    btn_login = types.InlineKeyboardButton('🔑 تسجيل الدخول', callback_data='login')
    markup.add(btn_upload, btn_settings, btn_help, btn_status, btn_login)
    
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
    
    bot.send_message(user_id, welcome_text, reply_markup=markup)

# معالجة الردود على الأزرار
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.message.chat.id
    if user_id not in user_data:
        user_data[user_id] = {
            'state': 'idle', 
            'delay': 5, 
            'channel': None, 
            'vcf_file': None,
            'client': None,
            'is_authenticated': False,
            'phone_numbers': [],
            'phone': None,
            'phone_code_hash': None
        }
    
    if call.data == 'upload_vcf':
        if not user_data[user_id].get('is_authenticated', False):
            bot.send_message(user_id, "⚠️ يجب عليك أولاً تسجيل الدخول من خلال زر 'تسجيل الدخول'")
            return
        ask_for_vcf(call.message)
    elif call.data == 'settings':
        show_settings(call.message)
    elif call.data == 'help':
        send_help(call.message)
    elif call.data == 'status':
        show_status(call.message)
    elif call.data == 'login':
        ask_for_phone(call.message)
    elif call.data == 'set_delay_1':
        set_delay(user_id, 1, call.message)
    elif call.data == 'set_delay_3':
        set_delay(user_id, 3, call.message)
    elif call.data == 'set_delay_5':
        set_delay(user_id, 5, call.message)
    elif call.data == 'set_delay_10':
        set_delay(user_id, 10, call.message)
    elif call.data == 'set_delay_custom':
        ask_custom_delay(call.message)
    elif call.data == 'set_channel':
        ask_for_channel(call.message)
    elif call.data == 'cancel':
        cancel_operation(call.message)
    elif call.data == 'back_to_main':
        send_welcome(call.message)
    elif call.data == 'start_adding':
        start_adding_members(call)

def ask_for_phone(message):
    user_id = message.chat.id
    user_data[user_id]['state'] = 'waiting_for_phone'
    
    markup = types.InlineKeyboardMarkup()
    btn_cancel = types.InlineKeyboardButton('❌ إلغاء', callback_data='cancel')
    markup.add(btn_cancel)
    
    bot.send_message(user_id, "📱 الرجاء إرسال رقم هاتفك مع رمز الدولة (مثال: +1234567890):", reply_markup=markup)

def ask_for_vcf(message):
    user_id = message.chat.id
    user_data[user_id]['state'] = 'waiting_for_vcf'
    
    markup = types.InlineKeyboardMarkup()
    btn_cancel = types.InlineKeyboardButton('❌ إلغاء', callback_data='cancel')
    markup.add(btn_cancel)
    
    bot.send_message(user_id, "📁 يرجى إرسال ملف جهات الاتصال بصيغة .vcf", reply_markup=markup)

def show_settings(message):
    user_id = message.chat.id
    delay = user_data[user_id]['delay']
    channel = user_data[user_id]['channel'] or "غير محدد"
    session_status = "✅ مفعلة" if user_data[user_id].get('is_authenticated', False) else "❌ غير مفعلة"
    
    settings_text = f"""
    ⚙️ الإعدادات الحالية:
    
    ⏱ تأخير بين الإضافات: {delay} ثانية
    📢 القناة المستهدفة: {channel}
    🔑 حالة التسجيل: {session_status}
    
    اختر أحد الخيارات أدناه لتغيير الإعدادات:
    """
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_delay_1 = types.InlineKeyboardButton('1 ثانية', callback_data='set_delay_1')
    btn_delay_3 = types.InlineKeyboardButton('3 ثواني', callback_data='set_delay_3')
    btn_delay_5 = types.InlineKeyboardButton('5 ثواني', callback_data='set_delay_5')
    btn_delay_10 = types.InlineKeyboardButton('10 ثواني', callback_data='set_delay_10')
    btn_custom_delay = types.InlineKeyboardButton('وقت مخصص', callback_data='set_delay_custom')
    btn_set_channel = types.InlineKeyboardButton('تعيين القناة', callback_data='set_channel')
    btn_login = types.InlineKeyboardButton('تسجيل الدخول', callback_data='login')
    btn_back = types.InlineKeyboardButton('🔙 رجوع', callback_data='back_to_main')
    
    markup.add(btn_delay_1, btn_delay_3, btn_delay_5, btn_delay_10, btn_custom_delay)
    markup.add(btn_set_channel, btn_login)
    markup.add(btn_back)
    
    bot.send_message(user_id, settings_text, reply_markup=markup)

def set_delay(user_id, delay, message):
    user_data[user_id]['delay'] = delay
    bot.send_message(user_id, f"✅ تم تعيين وقت الانتظار بين الإضافات إلى {delay} ثانية")
    show_settings(message)

def ask_custom_delay(message):
    user_id = message.chat.id
    user_data[user_id]['state'] = 'waiting_for_delay'
    
    markup = types.InlineKeyboardMarkup()
    btn_cancel = types.InlineKeyboardButton('❌ إلغاء', callback_data='cancel')
    markup.add(btn_cancel)
    
    bot.send_message(user_id, "⏱ الرجاء إدخال وقت الانتظار بين الإضافات (بالثواني):", reply_markup=markup)

def ask_for_channel(message):
    user_id = message.chat.id
    user_data[user_id]['state'] = 'waiting_for_channel'
    
    markup = types.InlineKeyboardMarkup()
    btn_cancel = types.InlineKeyboardButton('❌ إلغاء', callback_data='cancel')
    markup.add(btn_cancel)
    
    bot.send_message(user_id, "📢 الرجاء إرسال معرف القناة المستهدفة (مثال: @channel_username):", reply_markup=markup)

def send_help(message):
    help_text = """
    ❓ دليل استخدام البوت:
    
    1. انتقل إلى "تسجيل الدخول" وأدخل رقم هاتفك
    2. أدخل كود التحقق الذي ستستلمه على Telegram
    3. ارفع ملف VCF باستخدام الزر المخصص
    4. بعد معالجة الملف، سيطلب منك إدخال معرف القناة المستهدفة
    5. يمكنك ضبط وقت الانتظار بين كل إضافة من خلال قائمة الإعدادات
    6. ابدأ عملية الإضافة إلى القناة
    
    ⚠️ ملاحظات هامة:
    - تأكد من أن الحساب لديه صلاحية إضافة أعضاء إلى القنوات
    - الملف يجب أن يكون بصيغة .vcf صالحة
    - العملية قد تستغرق بعض الوقت حسب عدد الجهات
    - وقت الانتظار بين الإضافات يساعد على تجنب حظر الحساب
    
    📁 صيغة الملف: يجب أن يكون ملف VCF (vCard) يحتوي على أرقام الهواتف
    """
    
    markup = types.InlineKeyboardMarkup()
    btn_upload = types.InlineKeyboardButton('📤 رفع ملف الآن', callback_data='upload_vcf')
    btn_login = types.InlineKeyboardButton('🔑 تسجيل الدخول', callback_data='login')
    btn_back = types.InlineKeyboardButton('🔙 رجوع', callback_data='back_to_main')
    markup.add(btn_upload, btn_login, btn_back)
    
    bot.send_message(message.chat.id, help_text, reply_markup=markup)

def show_status(message):
    user_id = message.chat.id
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
    
    markup = types.InlineKeyboardMarkup()
    btn_back = types.InlineKeyboardButton('🔙 رجوع', callback_data='back_to_main')
    markup.add(btn_back)
    
    bot.send_message(user_id, status_text, reply_markup=markup)

def cancel_operation(message):
    user_id = message.chat.id
    user_data[user_id]['state'] = 'idle'
    
    # تنظيف الملف المؤقت إذا وجد
    if user_data[user_id].get('vcf_file') and os.path.exists(user_data[user_id]['vcf_file']):
        os.remove(user_data[user_id]['vcf_file'])
        user_data[user_id]['vcf_file'] = None
    
    bot.send_message(user_id, "تم إلغاء العملية. يمكنك البدء من جديد باستخدام /start")

# معالجة ملفات VCF
@bot.message_handler(content_types=['document'])
def handle_document(message):
    user_id = message.chat.id
    if user_id not in user_data:
        user_data[user_id] = {
            'state': 'idle', 
            'delay': 5, 
            'channel': None, 
            'vcf_file': None,
            'client': None,
            'is_authenticated': False,
            'phone_numbers': [],
            'phone': None,
            'phone_code_hash': None
        }
    
    # التحقق من حالة المستخدم
    if user_data[user_id]['state'] != 'waiting_for_vcf':
        bot.send_message(user_id, "يرجى البدء بالضغط على زر 'رفع ملف VCF' أولاً")
        return
    
    # التحقق من صيغة الملف
    file_name = message.document.file_name
    if not file_name.lower().endswith('.vcf'):
        bot.send_message(user_id, "الملف يجب أن يكون بصيغة .vcf. يرجى إرسال ملف صحيح.")
        return
    
    try:
        # تنزيل الملف
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # حفظ الملف مؤقتاً
        file_path = f"temp_{user_id}.vcf"
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)
        
        user_data[user_id]['vcf_file'] = file_path
        user_data[user_id]['state'] = 'processing_vcf'
        
        # معالجة الملف
        process_vcf_file(user_id, file_path)
        
    except Exception as e:
        logger.error(f"Error handling document: {e}")
        bot.send_message(user_id, "حدث خطأ أثناء معالجة الملف. يرجى المحاولة مرة أخرى.")

# معالجة الرسائل النصية
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_id = message.chat.id
    if user_id not in user_data:
        user_data[user_id] = {
            'state': 'idle', 
            'delay': 5, 
            'channel': None, 
            'vcf_file': None,
            'client': None,
            'is_authenticated': False,
            'phone_numbers': [],
            'phone': None,
            'phone_code_hash': None
        }
    
    text = message.text.strip()
    
    if user_data[user_id]['state'] == 'waiting_for_phone':
        # معالجة رقم الهاتف
        if re.match(r'^\+[1-9]\d{1,14}$', text):
            user_data[user_id]['phone'] = text
            user_data[user_id]['state'] = 'waiting_for_phone_code'
            
            # إرسال طلب كود التحقق
            asyncio.run(send_phone_code(user_id, text))
        else:
            bot.send_message(user_id, "❌ رقم الهاتف غير صالح. يرجى إدخال رقم مع رمز الدولة (مثال: +1234567890)")
    
    elif user_data[user_id]['state'] == 'waiting_for_phone_code':
        # معالجة كود التحقق
        if re.match(r'^\d{5}$', text):
            user_data[user_id]['state'] = 'verifying_code'
            asyncio.run(verify_phone_code(user_id, text))
        else:
            bot.send_message(user_id, "❌ كود التحقق يجب أن يكون 5 أرقام. يرجى المحاولة مرة أخرى.")
    
    elif user_data[user_id]['state'] == 'waiting_for_password':
        # معالجة كلمة المرور ثنائية
        if text:
            asyncio.run(verify_password(user_id, text))
        else:
            bot.send_message(user_id, "❌ يرجى إدخال كلمة المرور.")
    
    elif user_data[user_id]['state'] == 'waiting_for_channel':
        # معالجة اسم القناة
        if text.startswith('@'):
            user_data[user_id]['channel'] = text
            user_data[user_id]['state'] = 'idle'
            
            markup = types.InlineKeyboardMarkup()
            btn_start = types.InlineKeyboardButton('🚀 بدء الإضافة', callback_data='start_adding')
            btn_settings = types.InlineKeyboardButton('⚙️ الإعدادات', callback_data='settings')
            markup.add(btn_start, btn_settings)
            
            bot.send_message(user_id, f"✅ تم تعيين القناة المستهدفة: {text}\n\nيمكنك الآن ضبط الإعدادات أو بدء الإضافة.", reply_markup=markup)
        else:
            bot.send_message(user_id, "يجب أن يبدأ معرف القناة ب @. يرجى المحاولة مرة أخرى.")
    
    elif user_data[user_id]['state'] == 'waiting_for_delay':
        # معالجة وقت الانتظار المخصص
        try:
            delay = int(text)
            if delay < 1 or delay > 60:
                bot.send_message(user_id, "وقت الانتظار يجب أن يكون بين 1 و 60 ثانية.")
            else:
                user_data[user_id]['delay'] = delay
                user_data[user_id]['state'] = 'idle'
                bot.send_message(user_id, f"✅ تم تعيين وقت الانتظار إلى {delay} ثانية")
                show_settings(message)
        except ValueError:
            bot.send_message(user_id, "يرجى إدخال رقم صحيح للوقت.")

async def send_phone_code(user_id, phone):
    try:
        # إنشاء عميل جديد
        client = TelegramClient(f'sessions/{user_id}', API_ID, API_HASH)
        await client.connect()
        
        # إرسال طلب كود التحقق
        sent_code = await client.send_code_request(phone)
        user_data[user_id]['phone_code_hash'] = sent_code.phone_code_hash
        user_data[user_id]['client'] = client
        
        bot.send_message(user_id, "📲 تم إرسال كود التحقق إلى حسابك على Telegram. يرجى إدخال الكود المكون من 5 أرقام:")
        
    except FloodWaitError as e:
        bot.send_message(user_id, f"⏳ يجب الانتظار {e.seconds} ثانية قبل المحاولة مرة أخرى.")
        user_data[user_id]['state'] = 'idle'
    except PhoneNumberInvalidError:
        bot.send_message(user_id, "❌ رقم الهاتف غير صالح. يرجى المحاولة مرة أخرى.")
        user_data[user_id]['state'] = 'idle'
    except Exception as e:
        logger.error(f"Error sending phone code: {e}")
        bot.send_message(user_id, f"❌ حدث خطأ أثناء إرسال كود التحقق: {str(e)}")
        user_data[user_id]['state'] = 'idle'

async def verify_phone_code(user_id, code):
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
        
        bot.send_message(user_id, f"✅ تم تسجيل الدخول بنجاح!\n\n👤 الحساب: {me.first_name} {me.last_name or ''}\n📞 الهاتف: {me.phone}")
        
    except SessionPasswordNeededError:
        user_data[user_id]['state'] = 'waiting_for_password'
        bot.send_message(user_id, "🔒 حسابك محمي بكلمة مرور ثنائية. يرجى إدخال كلمة المرور:")
    except Exception as e:
        logger.error(f"Error verifying phone code: {e}")
        bot.send_message(user_id, f"❌ فشل التحقق من الكود: {str(e)}")
        user_data[user_id]['state'] = 'idle'

async def verify_password(user_id, password):
    try:
        client = user_data[user_id]['client']
        
        # التحقق من كلمة المرور
        await client.sign_in(password=password)
        
        # التحقق من نجاح التسجيل
        me = await client.get_me()
        user_data[user_id]['is_authenticated'] = True
        user_data[user_id]['state'] = 'idle'
        
        bot.send_message(user_id, f"✅ تم تسجيل الدخول بنجاح!\n\n👤 الحساب: {me.first_name} {me.last_name or ''}\n📞 الهاتف: {me.phone}")
        
    except Exception as e:
        logger.error(f"Error verifying password: {e}")
        bot.send_message(user_id, f"❌ فشل التحقق من كلمة المرور: {str(e)}")
        user_data[user_id]['state'] = 'idle'

def process_vcf_file(user_id, file_path):
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
            bot.send_message(user_id, "لم يتم العثور على أرقام هاتف صالحة في الملف.")
            return
        
        # حفظ الأرقام للمستخدم
        user_data[user_id]['phone_numbers'] = phone_numbers
        user_data[user_id]['state'] = 'waiting_for_channel'
        
        # طلب اسم القناة
        markup = types.InlineKeyboardMarkup()
        btn_cancel = types.InlineKeyboardButton('❌ إلغاء', callback_data='cancel')
        markup.add(btn_cancel)
        
        bot.send_message(user_id, f"✅ تم استخراج {len(phone_numbers)} رقم من الملف.\n\nالرجاء إرسال معرف القناة المستهدفة (مثال: @channel_username):", reply_markup=markup)
    
    except Exception as e:
        logger.error(f"Error processing VCF file: {e}")
        bot.send_message(user_id, "حدث خطأ أثناء معالجة الملف. يرجى التأكد من أن الملف صحيح.")
        
        # تنظيف الملف المؤقت
        if os.path.exists(file_path):
            os.remove(file_path)
        user_data[user_id]['vcf_file'] = None
        user_data[user_id]['state'] = 'idle'

# بدء عملية الإضافة إلى القناة
def start_adding_members(call):
    user_id = call.message.chat.id
    
    if not user_data[user_id].get('phone_numbers'):
        bot.send_message(user_id, "لا توجد أرقام للمعالجة. يرجى رفع ملف VCF أولاً.")
        return
    
    if not user_data[user_id].get('channel'):
        bot.send_message(user_id, "لم يتم تحديد قناة مستهدفة. يرجى تعيين القناة أولاً.")
        return
    
    if not user_data[user_id].get('is_authenticated', False):
        bot.send_message(user_id, "⚠️ يجب عليك أولاً تسجيل الدخول من خلال زر 'تسجيل الدخول'")
        return
    
    # بدء عملية الإضافة
    asyncio.run(add_members_to_channel(user_id))

async def add_members_to_channel(user_id):
    try:
        phone_numbers = user_data[user_id]['phone_numbers']
        channel = user_data[user_id]['channel']
        delay = user_data[user_id]['delay']
        
        # إرسال رسالة التقدم
        progress_msg = bot.send_message(user_id, f"جاري البدء بإضافة {len(phone_numbers)} عضو إلى {channel}...")
        
        added_count = 0
        skipped_count = 0
        error_count = 0
        
        # استخدام جلسة المستخدم
        async with user_data[user_id]['client'] as client:
            for i, phone in enumerate(phone_numbers):
                try:
                    # تحديث رسالة التقدم
                    if i % 10 == 0 or i == len(phone_numbers) - 1:
                        bot.edit_message_text(
                            f"جاري المعالجة: {i+1}/{len(phone_numbers)}\nتمت الإضافة: {added_count}\nتم التخطي: {skipped_count}\nأخطاء: {error_count}",
                            user_id, progress_msg.message_id
                        )
                    
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
                            bot.edit_message_text(
                                f"تم الوصول إلى حد الإضافات. جاري الانتظار {wait_time} ثانية...",
                                user_id, progress_msg.message_id
                            )
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
            
            markup = types.InlineKeyboardMarkup()
            btn_new_file = types.InlineKeyboardButton('📤 ملف جديد', callback_data='upload_vcf')
            btn_settings = types.InlineKeyboardButton('⚙️ الإعدادات', callback_data='settings')
            markup.add(btn_new_file, btn_settings)
            
            bot.edit_message_text(result_text, user_id, progress_msg.message_id, reply_markup=markup)
    
    except Exception as e:
        logger.error(f"Error in add_members_to_channel: {e}")
        bot.send_message(user_id, f"حدث خطأ أثناء عملية الإضافة: {str(e)}")

# إنشاء مجلد الجلسات إذا لم يكن موجوداً
if not os.path.exists('sessions'):
    os.makedirs('sessions')

# تشغيل البوت
if __name__ == '__main__':
    print("جارٍ تشغيل البوت...")
    bot.infinity_polling()
