# 🐙 ساخت مخزن GitHub و push کردن (گام‌به‌گام)

<div dir="rtl">

پروژه آماده است؛ فقط باید به GitHub ببری تا APK ساخته شود و کدت پشتیبان داشته باشد.

## قدم ۱: حساب GitHub

1. به https://github.com برو و ثبت‌نام کن (رایگان)
2. تأیید ایمیل را انجام بده

## قدم ۲: ساخت مخزن خالی

1. دکمه **+** بالای صفحه → **New repository**
2. نام: `sarkhat-bot`
3. **Private** انتخاب کن (کد شخصی توست) — یا Public اگر می‌خواهی بقیه ببینند
4. تیک «Add a README» را **نزن** (پروژه README خودش را دارد)
5. **Create repository**

## قدم ۳: آپلود (دو راه)

### راه آسان — بدون ترمینال:

در صفحه مخزن خالی، لینک **«uploading an existing file»** را بزن و همه فایل‌های پروژه را
بکش و رها کن (فولدرهای data و output را نفرست). بعد **Commit changes** بزن.

> نکته: GitHub در آپلود مرورگری، پوشه خالی را جا می‌اندازد — مشکلی نیست.

### راه استاندارد — با git:

```bash
cd sarkhat-bot
git init
git add .
git commit -m "ربات سرخط‌زن نسخه ۲ — اپ موبایل + سیگنال اعتبارسنجی‌شده"
git branch -M main
git remote add origin https://github.com/<نام‌کاربری>/sarkhat-bot.git
git push -u origin main
```

گیت‌هاب ازت username/password می‌پرسد — به‌جای password باید
**Personal Access Token** بدهی: Settings → Developer settings →
Personal access tokens → Tokens (classic) → Generate new token → تیک `repo`.

## قدم ۴: APK بگیر

1. تب **Actions** مخزن را باز کن
2. workflow «ساخت APK اندروید» باید خودکار اجرا شده باشد
3. بعد از تیک سبز، ران را باز → پایین → **Artifacts** → دانلود `SarkhatBot-APK`

## قدم ۵ (اختیاری): فعال‌کردن وب‌اپ

1. Settings → Pages
2. Source: **GitHub Actions**
3. workflow «انتشار وب‌اپ» را اجرا کن (Run workflow)
4. آدرس اپ: `https://<نام‌کاربری>.github.io/sarkhat-bot/`

## به‌روزرسانی بعدی

هر وقت فایلی را تغییر دادی، همان دو دستور:

```bash
git add .
git commit -m "توضیح تغییر"
git push
```

هر push جدید = ساخت APK جدید در Actions.

</div>
