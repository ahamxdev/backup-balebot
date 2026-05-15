# Bale Backup Bot (Production-ready)

این سرویس یک Worker دائمی برای بله است که:

- مسیر بکاپ (`/backup`) را مانیتور می‌کند.
- فقط فایل‌هایی که **ثابت** شده‌اند (در حال نوشته‌شدن نیستند) را ارسال می‌کند.
- فایل را به پیوی شما در بله ارسال می‌کند (`sendDocument`).
- بعد از ارسال موفق، همان فایل را حذف می‌کند.
- اگر سرویس ری‌استارت شود، فایل‌های نیمه‌کاره (`.uploading`) را Recover می‌کند.
- با Lock File از اجرای هم‌زمان چند نمونه جلوگیری می‌کند.

## معماری امن برای Production

سناریویی که پیاده شده:

1. اسکن دوره‌ای دایرکتوری (پایدارتر از event-only در محیط‌های مختلف سرور)
2. بررسی پایداری فایل (Size/Mtime باید برای `STABLE_SECONDS` ثابت بماند)
3. Claim اتمیک فایل با `rename -> *.uploading`
4. ارسال با API بله
5. حذف فایل بعد از موفقیت
6. در خطا، فایل به نام اصلی برگردانده می‌شود تا retry شود

این مدل در عمل برای بکاپ‌های دیتابیس قابل اعتمادتر از watcher-only است.

## نصب

```bash
cd /home/ahamxdev/Files/Workspace/personal/backup-balebot
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## تنظیم `.env`

فایل `.env` ساخته شده و توکن شما داخل آن قرار گرفته است. فقط این مقدار را تکمیل کنید:

```env
BALE_TARGET_CHAT_ID=
```

بقیه تنظیمات مهم:

- `BACKUP_DIR=/backup`
- `BACKUP_FILE_PATTERNS=*.bak,*.backup,*.dump,*.gz,*.sql,*.sql.gz,*.tar,*.tar.gz,*.xz,*.zip,*.zst`
- `STABLE_SECONDS=20`
- `SCAN_INTERVAL_SECONDS=5`
- `SERVER_PUBLIC_IP=` اگر خالی باشد، سرویس خودش IP پابلیک را lookup می‌کند.
- `PUBLIC_IP_LOOKUP_URL=https://api.ipify.org`

کپشن پیش‌فرض هر فایل کوتاه ولی کاربردی است:

```text
Backup uploaded
File: db.sql.gz
Size: 12.34 MB
Server: my-server
Public IP: 203.0.113.10
Backup mtime: 2026-05-16 02:30:12 +0330
Sent at: 2026-05-16 02:30:35 +0330
```

اگر خواستی متن کپشن را عوض کنی، `CAPTION_TEMPLATE` را در `.env` تغییر بده. placeholderهای قابل استفاده:

```text
{filename}
{file_size}
{size_human}
{hostname}
{public_ip}
{backup_dir}
{file_modified_at}
{sent_at}
```

## گرفتن `chat_id`

1. در بله به بات یک پیام بفرستید.
2. دستور زیر را اجرا کنید:

```bash
python scripts/get_chat_id.py --env-file .env
```

3. مقدار نشان‌داده‌شده را در `BALE_TARGET_CHAT_ID` قرار دهید.

## اجرای دستی

```bash
source .venv/bin/activate
bale-backup-bot --env-file .env
```

## اجرای دائمی با systemd

فایل نمونه: `systemd/bale-backup-bot.service`

1. مسیرها و کاربر را متناسب با سرور خودتان اصلاح کنید.
2. فایل را در `/etc/systemd/system/` کپی کنید.
3. سپس:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bale-backup-bot.service
sudo systemctl status bale-backup-bot.service
journalctl -u bale-backup-bot.service -f
```

## نکات مهم

- محدودیت ارسال فایل طبق مستند بله در حال حاضر 50MB است (`MAX_FILE_SIZE_MB=50`).
- اگر فایل از حد بیشتر باشد، ارسال نمی‌شود و در لاگ خطا ثبت می‌شود.
- برای polling، روی بات webhook فعال نباشد. سرویس هنگام استارت `deleteWebhook` را صدا می‌زند.

## مستند مرجع بله

- https://docs.bale.ai/
- Endpoint pattern: `https://tapi.bale.ai/bot<token>/METHOD_NAME`
