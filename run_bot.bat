@echo off
cd /d C:\Users\hp\Desktop\Guder Bingo
set BOT_TOKEN=8813599415:AAE1t6JJg8zgZFs5Osh5ukaRN8N4d5je9yM
set WEB_APP_URL=https://guder-bingo-server.onrender.com/
set ADMIN_API_KEY=my_secure_admin_key
set PAYMENT_PHONE=0913706670
set SUPPORT_USERNAME=Guderbingosupport
set BOT_USERNAME=guderbingo_bot
echo Environment variables loaded successfully!
python bot.py
pause