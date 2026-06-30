import os
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
import anthropic

# ─── Логирование ───────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Ключи из переменных среды Render.com ──────────────────────
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ADMIN_ID          = int(os.environ.get("ADMIN_ID", "0"))

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── Тарифы ────────────────────────────────────────────────────
PLANS = {
    "standard": {"name": "Standard",  "price": "2 990 тг/мес", "checks": 30,  "premium": False},
    "premium":  {"name": "Premium",   "price": "7 990 тг/мес", "checks": 90,  "premium": True},
}

# ─── БД (JSON-файл) ────────────────────────────────────────────
DB_FILE = "users.json"

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE) as f:
            return json.load(f)
    return {}

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def get_user(uid: str) -> dict:
    db = load_db()
    if uid not in db:
        db[uid] = {
            "checks_used": 0,
            "free_limit":  2,
            "paid":        False,
            "premium":     False,
            "paid_checks": 0,
            "joined":      datetime.now().isoformat()
        }
        save_db(db)
    return db[uid]

def update_user(uid: str, data: dict):
    db = load_db()
    db[uid].update(data)
    save_db(db)

def can_check(uid: str) -> bool:
    u = get_user(uid)
    if u["paid"] and u["paid_checks"] > 0:
        return True
    return u["checks_used"] < u["free_limit"]

def use_check(uid: str):
    u = get_user(uid)
    if u["paid"] and u["paid_checks"] > 0:
        update_user(uid, {"paid_checks": u["paid_checks"] - 1})
    else:
        update_user(uid, {"checks_used": u["checks_used"] + 1})

def is_premium(uid: str) -> bool:
    return get_user(uid).get("premium", False)

# ─── Промпты ───────────────────────────────────────────────────
PROMPT_CHECK = """Ты — строгий экзаменатор IELTS Writing Task 2. Оценивай честно.

Критерии:
1. Task Response (TR) — тема, позиция, аргументы
2. Coherence & Cohesion (CC) — логика, структура
3. Lexical Resource (LR) — словарный запас
4. Grammatical Range & Accuracy (GRA) — грамматика

ФОРМАТ (строго):

📊 ОБЩИЙ БАЛЛ: X.X / 9.0

━━━━━━━━━━━━━━━━━━━━
📌 Task Response: X.X
[2-3 предложения]

📌 Coherence & Cohesion: X.X
[2-3 предложения]

📌 Lexical Resource: X.X
[2-3 предложения + примеры слов из эссе]

📌 Grammatical Range & Accuracy: X.X
[2-3 предложения + 2-3 ошибки с исправлениями]
━━━━━━━━━━━━━━━━━━━━

✅ Сильные стороны:
• [пункт 1]
• [пункт 2]

🔧 Что улучшить:
• [главное]
• [второе]

💡 Совет:
[1 конкретный совет]

Не завышай оценки. Среднее эссе — 5.5–6.0."""

PROMPT_POLISH = """Ты — опытный редактор IELTS эссе. Улучши эссе студента, сохранив его идеи и структуру.

Задача:
1. Улучши лексику — замени простые слова на академические синонимы
2. Исправь грамматические ошибки
3. Улучши связность — добавь/улучши linking words
4. Сохрани оригинальную позицию и аргументы автора

ФОРМАТ ОТВЕТА:

✨ УЛУЧШЕННАЯ ВЕРСИЯ:

[полный текст улучшенного эссе]

━━━━━━━━━━━━━━━━━━━━
📝 ЧТО ИЗМЕНЕНО:

• [изменение 1 — конкретно: "заменил X на Y потому что..."]
• [изменение 2]
• [изменение 3]
• [ещё изменения...]

🎯 ОЖИДАЕМЫЙ ПРИРОСТ БАЛЛА: +0.5 — +1.0"""

# ─── /start ────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = str(update.effective_user.id)
    user = get_user(uid)
    name = update.effective_user.first_name or "друг"
    free_left = max(0, user["free_limit"] - user["checks_used"])

    text = (
        f"👋 Привет, {name}\\!\n\n"
        f"Я — AI\\-проверяющий IELTS Writing Task 2\\.\n"
        f"Оцениваю эссе по 4 критериям экзаменатора за 15 секунд\\.\n\n"
        f"📋 *Как пользоваться:*\n"
        f"Просто отправь своё эссе в этот чат\\.\n\n"
        f"🎁 Бесплатных проверок: *{free_left} из 2*\n"
        f"💳 Standard — 2 990 тг/мес \\(30 проверок\\)\n"
        f"👑 Premium — 7 990 тг/мес \\(90 проверок \\+ /polish\\)\n\n"
        f"Напиши /buy чтобы выбрать тариф\\."
    )

    kb = [[
        InlineKeyboardButton("📋 Тарифы", callback_data="buy"),
        InlineKeyboardButton("❓ Помощь",  callback_data="help"),
    ]]
    await update.message.reply_text(text, parse_mode="MarkdownV2",
                                    reply_markup=InlineKeyboardMarkup(kb))

# ─── /help ─────────────────────────────────────────────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📚 *Как пользоваться:*\n\n"
        "1️⃣ Отправь эссе в чат — получишь оценку\n"
        "2️⃣ /status — сколько проверок осталось\n"
        "3️⃣ /buy — купить подписку\n\n"
        "👑 *Premium-команды:*\n"
        "/polish — AI улучшает твоё эссе\n"
        "_(напиши /polish и затем эссе одним сообщением)_\n\n"
        "📊 *Критерии оценки:*\n"
        "• TR — раскрытие темы\n"
        "• CC — логика и структура\n"
        "• LR — словарный запас\n"
        "• GRA — грамматика\n\n"
        "📞 Поддержка: @ieltsmindset\\_kz"
    )
    msg = update.message or update.callback_query.message
    await msg.reply_text(text, parse_mode="Markdown")

# ─── /buy ──────────────────────────────────────────────────────
async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id if update.message else update.callback_query.from_user.id)
    text = (
        "💳 *Тарифы IELTSmindset AI:*\n\n"
        "🥉 *Standard — 2 990 тг/мес*\n"
        "   • 30 проверок эссе\n"
        "   • История проверок\n"
        "   • Советы экзаменатора\n\n"
        "👑 *Premium — 7 990 тг/мес*\n"
        "   • 90 проверок эссе\n"
        "   • Команда /polish \\(AI улучшает эссе\\)\n"
        "   • Персональные шаблоны\n"
        "   • Приоритетная поддержка\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📲 *Как оплатить:*\n"
        f"1\\. Переведи на Kaspi: *\\+7 702 567 61 05*\n"
        f"2\\. В комментарии напиши свой ID: `{uid}`\n"
        "3\\. Пришли скриншот в этот чат\n\n"
        "⏰ Доступ открывается за 15 минут"
    )
    kb = [[
        InlineKeyboardButton("🥉 Standard (2 990 тг)", callback_data="plan_standard"),
        InlineKeyboardButton("👑 Premium (7 990 тг)",  callback_data="plan_premium"),
    ]]
    msg = update.message or update.callback_query.message
    await msg.reply_text(text, parse_mode="MarkdownV2",
                         reply_markup=InlineKeyboardMarkup(kb))

# ─── /status ───────────────────────────────────────────────────
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = str(update.effective_user.id)
    user = get_user(uid)
    free_left = max(0, user["free_limit"] - user["checks_used"])

    if user["paid"] and user["premium"]:
        tier = "👑 Premium"
    elif user["paid"]:
        tier = "🥉 Standard"
    else:
        tier = "🆓 Бесплатный"

    checks_left = user["paid_checks"] if user["paid"] else free_left

    await update.message.reply_text(
        f"📋 *Твой статус:*\n\n"
        f"Тариф: {tier}\n"
        f"Осталось проверок: *{checks_left}*\n"
        f"Всего сделано: {user['checks_used']}",
        parse_mode="Markdown"
    )

# ─── /polish (только Premium) ──────────────────────────────────
async def polish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = str(update.effective_user.id)

    if not is_premium(uid):
        kb = [[InlineKeyboardButton("👑 Купить Premium", callback_data="plan_premium")]]
        await update.message.reply_text(
            "👑 *Команда /polish доступна только в Premium*\n\n"
            "Premium — 7 990 тг/мес:\n"
            "• 90 проверок эссе\n"
            "• AI улучшает твоё эссе (/polish)\n"
            "• Персональные шаблоны",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    # Берём текст после команды /polish
    essay_text = update.message.text.replace("/polish", "", 1).strip()

    if not essay_text:
        await update.message.reply_text(
            "✏️ Напиши эссе сразу после команды:\n\n"
            "/polish [твоё эссе здесь]"
        )
        return

    if len(essay_text.split()) < 80:
        await update.message.reply_text("⚠️ Эссе слишком короткое. Минимум 80 слов.")
        return

    await update.message.reply_text(
        "✨ *Улучшаю эссе...*\n\nОбычно занимает 20–30 секунд ⏳",
        parse_mode="Markdown"
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=PROMPT_POLISH,
            messages=[{"role": "user", "content": f"Улучши это IELTS эссе:\n\n{essay_text}"}]
        )
        result = response.content[0].text
        # Telegram ограничение 4096 символов — делим если нужно
        if len(result) > 4000:
            await update.message.reply_text(result[:4000])
            await update.message.reply_text(result[4000:])
        else:
            await update.message.reply_text(result)
    except Exception as e:
        logger.error(f"Polish error: {e}")
        await update.message.reply_text("❌ Ошибка. Попробуй ещё раз.")

# ─── /approve (только админ) ───────────────────────────────────
async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа.")
        return

    # Использование: /approve [user_id] [standard|premium]
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Использование:\n"
            "/approve [user\\_id] standard\n"
            "/approve [user\\_id] premium",
            parse_mode="Markdown"
        )
        return

    target_id  = context.args[0]
    plan_key   = context.args[1].lower()

    if plan_key not in PLANS:
        await update.message.reply_text("❌ Тариф не найден. Используй: standard или premium")
        return

    plan = PLANS[plan_key]
    user = get_user(target_id)
    update_user(target_id, {
        "paid":        True,
        "premium":     plan["premium"],
        "paid_checks": user.get("paid_checks", 0) + plan["checks"]
    })

    plan_label = "👑 Premium" if plan["premium"] else "🥉 Standard"
    polish_note = "\n✨ Команда /polish доступна!" if plan["premium"] else ""

    try:
        await context.bot.send_message(
            chat_id=int(target_id),
            text=(
                f"✅ *Доступ активирован!*\n\n"
                f"Тариф: {plan_label}\n"
                f"Проверок добавлено: {plan['checks']}{polish_note}\n\n"
                f"Отправляй эссе — начинаем! 🚀"
            ),
            parse_mode="Markdown"
        )
    except Exception:
        pass  # Студент ещё не написал боту

    await update.message.reply_text(
        f"✅ Активировано:\n"
        f"Пользователь: {target_id}\n"
        f"Тариф: {plan_label}\n"
        f"Проверок: {plan['checks']}"
    )

# ─── /stats (только админ) ─────────────────────────────────────
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа.")
        return

    db = load_db()
    total   = len(db)
    paid    = sum(1 for u in db.values() if u.get("paid"))
    premium = sum(1 for u in db.values() if u.get("premium"))
    checks  = sum(u.get("checks_used", 0) for u in db.values())

    await update.message.reply_text(
        f"📊 *Статистика бота:*\n\n"
        f"👥 Всего пользователей: {total}\n"
        f"🥉 Standard: {paid - premium}\n"
        f"👑 Premium: {premium}\n"
        f"📝 Проверок всего: {checks}",
        parse_mode="Markdown"
    )

# ─── Кнопки ────────────────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "buy":
        await buy(update, context)
    elif query.data == "help":
        await help_command(update, context)
    elif query.data in ("plan_standard", "plan_premium"):
        uid = str(query.from_user.id)
        plan = query.data.replace("plan_", "")
        plan_info = PLANS[plan]
        await query.message.reply_text(
            f"✅ Отлично\\! Ты выбрал *{plan_info['name']}*\n\n"
            f"💳 Сумма: *{plan_info['price']}*\n\n"
            f"Переведи на Kaspi: *\\+7 702 567 61 05*\n"
            f"В комментарии напиши свой ID: `{uid}`\n\n"
            f"Пришли скриншот — доступ за 15 минут ⏰",
            parse_mode="MarkdownV2"
        )

# ─── Проверка эссе (основной обработчик) ───────────────────────
async def check_essay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = str(update.effective_user.id)
    text = update.message.text.strip()

    word_count = len(text.split())
    if word_count < 80:
        await update.message.reply_text(
            f"⚠️ Эссе слишком короткое ({word_count} слов).\n"
            "Для IELTS Writing Task 2 нужно минимум 250 слов.\n"
            "Отправь полное эссе."
        )
        return

    if not can_check(uid):
        kb = [[InlineKeyboardButton("💳 Выбрать тариф", callback_data="buy")]]
        await update.message.reply_text(
            "🔒 *Бесплатные проверки исчерпаны*\n\n"
            "Выбери тариф чтобы продолжить:\n"
            "• 🥉 Standard — 2 990 тг/мес → 30 проверок\n"
            "• 👑 Premium — 7 990 тг/мес → 90 проверок + /polish",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    await update.message.reply_text(
        "🔍 *Анализирую эссе...*\n\nПроверяю по 4 критериям IELTS\\. Обычно 10–20 секунд ⏳",
        parse_mode="MarkdownV2"
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=PROMPT_CHECK,
            messages=[{"role": "user", "content": f"Проверь IELTS Writing Task 2:\n\n{text}"}]
        )
        result = response.content[0].text
        use_check(uid)

        user = get_user(uid)
        if user["paid"]:
            remaining = f"\n\n📊 Осталось проверок: {user['paid_checks']}"
            if user["premium"]:
                remaining += "\n✨ Хочешь улучшить эссе? Используй /polish"
        else:
            free_left = max(0, user["free_limit"] - user["checks_used"])
            remaining = f"\n\n🆓 Бесплатных проверок осталось: {free_left}"
            if free_left == 0:
                remaining += "\n\nНапиши /buy чтобы продолжить."

        await update.message.reply_text(result + remaining)

    except Exception as e:
        logger.error(f"Check error: {e}")
        await update.message.reply_text("❌ Ошибка при проверке. Попробуй через минуту.")

# ─── Запуск ────────────────────────────────────────────────────
def main():
    # Явно создаём event loop — нужно для Python 3.14, где
    # asyncio.get_event_loop() больше не создаёт loop автоматически
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("help",    help_command))
    app.add_handler(CommandHandler("status",  status))
    app.add_handler(CommandHandler("buy",     buy))
    app.add_handler(CommandHandler("polish",  polish))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("stats",   stats))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_essay))

    logger.info("Бот запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
