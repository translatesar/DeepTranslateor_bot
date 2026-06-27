import os
import re
import asyncio
import urllib.request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import fitz  # PyMuPDF
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from deep_translator import GoogleTranslator
from openai import AsyncOpenAI
import arabic_reshaper
from bidi.algorithm import get_display

# ============ تنظیمات اصلی ============
TOKEN = os.environ.get('BOT_TOKEN')
PORT = int(os.environ.get('PORT', 8080))

# ============ تنظیم فونت (دانلود در پوشه موقت ریل‌وی) ============
FONT_DIR = '/tmp/fonts'
os.makedirs(FONT_DIR, exist_ok=True)

FONT_READY = False
try:
    font_path = f'{FONT_DIR}/Vazir.ttf'
    if not os.path.exists(font_path):
        print("⏳ در حال دانلود فونت وزیرمتن...")
        urllib.request.urlretrieve(
            'https://github.com/rastikerdar/vazir-font/raw/master/dist/Vazir.ttf',
            font_path
        )
    pdfmetrics.registerFont(TTFont('Vazir', font_path))
    FONT_READY = True
    print("✅ فونت با موفقیت بارگذاری شد")
except Exception as e:
    print(f"❌ خطا فونت: {e}")

# ============ مترجم هوشمند (دیپ‌سیک + پشتیبان گوگل) ============
class SmartTranslator:
    def __init__(self):
        self.ai_client = AsyncOpenAI(
            api_key=os.environ.get('DEEPSEEK_API_KEY'),
            base_url="https://api.deepseek.com/v1",
            timeout=30.0
        )
        
    def postprocess(self, text):
        """اصلاحات نگارشی پس از ترجمه"""
        text = re.sub(r'\bمی\s+', 'می‌', text)
        text = re.sub(r'\bنمی\s+', 'نمی‌', text)
        text = re.sub(r'\bبی\s+', 'بی‌', text)
        text = text.replace('?', '؟').replace(';', '؛')
        text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text) # حذف تکرار
        return text

    async def translate_text(self, text, target_lang='fa'):
        if not text or not text.strip(): return None
        
        lang_map = {'fa': 'فارسی', 'ar': 'عربی', 'en': 'انگلیسی', 'fr': 'فرانسوی', 'de': 'آلمانی'}
        target_name = lang_map.get(target_lang, target_lang)

        # تلاش اول: هوش مصنوعی دیپ‌سیک
        ai_result = await self._translate_with_ai(text, target_name)
        if ai_result:
            return self.postprocess(ai_result)
            
        # تلاش دوم: گوگل (در صورت خطای AI)
        print("⚠️ خطا در دیپ‌سیک، استفاده از گوگل...")
        try:
            translator = GoogleTranslator(source='auto', target=target_lang)
            result = translator.translate(text[:4500])
            return self.postprocess(result) if result else None
        except Exception as e:
            print(f"Google Error: {e}")
            return None

    async def _translate_with_ai(self, text, target_name):
        """ترجمه با استفاده از DeepSeek V3"""
        try:
            prompt = f"""
            تو یک مترجم حرفه‌ای و بسیار بومی زبان {target_name} هستی.
            متن زیر را ترجمه کن. قوانین:
            1. ترجمه باید کاملاً روان، طبیعی و مانند یک فرد بومی باشد.
            2. اصطلاحات و عبارات را به درستی معادل‌سازی کن.
            3. هیچ توضیح اضافه‌ای نده، فقط متن ترجمه شده خالص را برگردان.
            
            متن:
            {text}
            """
            
            response = await self.ai_client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            print(f"DeepSeek Error: {e}")
            return None

smart_translator = SmartTranslator()

# ============ توابع PDF ============
def fix_rtl(text):
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)

def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    pages = [{'number': i + 1, 'text': page.get_text()} for i, page in enumerate(doc)]
    doc.close()
    return pages

def create_translated_pdf(output_path, translated_pages, target_lang):
    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4
    is_rtl = target_lang in ['fa', 'ar', 'ur']
    font_name = 'Vazir' if (is_rtl and FONT_READY) else 'Helvetica'
    
    for page_num, page_data in enumerate(translated_pages):
        if page_num > 0: c.showPage()
        
        c.setFillColorRGB(0.12, 0.28, 0.53)
        c.roundRect(20, height - 55, width - 40, 40, 10, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont(font_name, 12)
        header_text = fix_rtl(f"صفحه {page_num + 1} از {len(translated_pages)}") if is_rtl else f"Page {page_num + 1}"
        if is_rtl: c.drawRightString(width - 40, height - 40, header_text)
        else: c.drawString(40, height - 40, header_text)

        y = height - 85
        if page_data:
            c.setFillColorRGB(0.4, 0.4, 0.4); c.setFont(font_name, 8)
            orig_title = fix_rtl("متن اصلی:") if is_rtl else "Original:"
            if is_rtl: c.drawRightString(width - 50, y, orig_title)
            else: c.drawString(50, y, orig_title)
            
            y -= 20; c.setFillColorRGB(0.2, 0.2, 0.2); c.setFont(font_name, 9)
            for line in page_data['original'].split('\n')[:20]:
                if y < 150: c.showPage(); y = height - 70
                display_line = fix_rtl(line.strip()) if is_rtl else line.strip()
                if is_rtl: c.drawRightString(width - 50, y, display_line)
                else: c.drawString(50, y, display_line)
                y -= 14

            y -= 10; c.setStrokeColorRGB(0.85, 0.2, 0.2); c.setLineWidth(1.5); c.line(50, y, width - 50, y); y -= 25

            c.setFillColorRGB(0.12, 0.28, 0.53); c.setFont(font_name, 8)
            trans_title = fix_rtl("ترجمه هوشمند:") if is_rtl else "AI Translation:"
            if is_rtl: c.drawRightString(width - 50, y, trans_title)
            else: c.drawString(50, y, trans_title)
            
            y -= 20; c.setFillColorRGB(0, 0, 0); c.setFont(font_name, 10)
            for line in page_data['translated'].split('\n'):
                if y < 60: c.showPage(); y = height - 70
                display_line = fix_rtl(line.strip()) if is_rtl else line.strip()
                if is_rtl: c.drawRightString(width - 50, y, display_line)
                else: c.drawString(50, y, display_line)
                y -= 16
    c.save()

# ============ هندلرهای ربات ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌟 *ربات مترجم هوشمند (DeepSeek V3)*\n\n"
        "✅ متن بفرستید یا فایل PDF/TXT ارسال کنید.\n"
        "🧠 ترجمه کاملاً روان و طبیعی با هوش مصنوعی!\n\n"
        "⚙️ تنظیم زبان مقصد: `/setlang en`",
        parse_mode='Markdown'
    )

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return await update.message.reply_text("❌ مثال: `/setlang en`", parse_mode='Markdown')
    context.user_data['target_lang'] = context.args[0].lower()
    await update.message.reply_text(f"✅ زبان مقصد تنظیم شد: {context.args[0].upper()}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if len(text) < 2: return
    target_lang = context.user_data.get('target_lang', 'fa')
    msg = await update.message.reply_text("🧠 در حال ترجمه با هوش مصنوعی...")
    
    # فراخوانی مستقیم (چون تابع ما Async است)
    result = await smart_translator.translate_text(text, target_lang)
    
    if result: await msg.edit_text(f"🌍 *ترجمه:*\n\n{result}", parse_mode='Markdown')
    else: await msg.edit_text("❌ خطا در ترجمه.")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    filename = update.message.document.file_name.lower()
    if not (filename.endswith('.pdf') or filename.endswith('.txt')):
        return await update.message.reply_text("❌ فقط PDF و TXT پشتیبانی می‌شود.")
    
    if update.message.document.file_size > 5 * 1024 * 1024:
        return await update.message.reply_text("❌ حجم فایل بیش از ۵ مگابایت است!")

    msg = await update.message.reply_text("⏳ دریافت فایل...")
    target_lang = context.user_data.get('target_lang', 'fa')
    temp_input = f"/tmp/input_{update.message.message_id}.{filename.split('.')[-1]}"
    temp_output = f"/tmp/out_{update.message.message_id}.pdf"

    try:
        file = await context.bot.get_file(update.message.document.file_id)
        await file.download_to_drive(temp_input)
        
        if filename.endswith('.txt'):
            with open(temp_input, 'r', encoding='utf-8') as f: text = f.read()
            await msg.edit_text("🧠 ترجمه هوشمند فایل متنی...")
            translated = await smart_translator.translate_text(text, target_lang)
            if translated: create_translated_pdf(temp_output, [{'original': text, 'translated': translated}], target_lang)
            else: raise Exception("ترجمه ناموفق بود")

        elif filename.endswith('.pdf'):
            pages = extract_text_from_pdf(temp_input)
            if len(pages) > 10: pages = pages[:10]
            translated_pages = []
            for i, page in enumerate(pages):
                await msg.edit_text(f"🧠 ترجمه هوشمند صفحه {i+1} از {len(pages)}...")
                text = page['text'].strip()
                if text:
                    trans = await smart_translator.translate_text(text[:3000], target_lang)
                    translated_pages.append({'original': text[:2000], 'translated': trans if trans else '⚠️ خطا'})
            await msg.edit_text("📝 ساخت PDF نهایی...")
            create_translated_pdf(temp_output, translated_pages, target_lang)

        with open(temp_output, 'rb') as f:
            await update.message.reply_document(document=f, filename=f"translated_{filename.split('.')[0]}.pdf", caption="✅ ترجمه هوشمند انجام شد!")
    except Exception as e:
        await msg.edit_text(f"❌ خطا: {str(e)[:200]}")
    finally:
        if os.path.exists(temp_input): os.remove(temp_input)
        if os.path.exists(temp_output): os.remove(temp_output)
        try: await msg.delete()
        except: pass

# ============ اجرای وب‌هوک مخصوص Railway ============
async def main():
    if not TOKEN:
        print("❌ توکن ربات تنظیم نشده است!")
        return

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("setlang", set_language))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    railway_url = os.environ.get('RAILWAY_PUBLIC_URL')
    if not railway_url:
        print("❌ RAILWAY_PUBLIC_URL پیدا نشد.")
        return

    webhook_path = f"/webhook/{TOKEN}"
    await app.bot.set_webhook(url=f"{railway_url}{webhook_path}")
    print(f"✅ وب‌هوک تنظیم شد: {railway_url}{webhook_path}")

    await app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path
    )

if __name__ == '__main__':
    asyncio.run(main())
