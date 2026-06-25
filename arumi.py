import discord
import asyncio
import io
import os
import threading
import logging
from collections import deque
from datetime import datetime
from discord.ext import commands
from discord import app_commands
from flask import Flask
from waitress import serve
import aioconsole

# ---------------------------------------------------------------------------
# КОНФИГУРАЦИЯ — задавай через переменные окружения, НЕ хардкодь в коде
# ---------------------------------------------------------------------------
TOKEN               = os.environ.get("BOT_TOKEN", "")
LOG_CHANNEL_ID      = int(os.environ.get("LOG_CHANNEL_ID",      "1516571996287270913"))
TICKET_ARCHIVE_ID   = int(os.environ.get("TICKET_ARCHIVE_ID",   "1516572034329739305"))
TICKET_CATEGORY_ID  = int(os.environ.get("TICKET_CATEGORY_ID",  "1516603442918068244"))
AUTO_ROLE_IDS       = [
    1516571911084310720
]
WEB_PORT = int(os.environ.get("WEB_PORT", "5000"))

if not TOKEN:
    raise RuntimeError(
        "Токен не задан! Установи переменную окружения BOT_TOKEN."
    )

# ---------------------------------------------------------------------------
# ЛОГИРОВАНИЕ
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ArumiBot")

# Thread-safe кольцевой буфер для веб-дашборда
_log_lock = threading.Lock()
web_logs: deque[dict] = deque(maxlen=50)


def add_web_log(text: str, level: str = "info") -> None:
    """Добавляет запись в консоль и в буфер веб-дашборда."""
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "text": text,
        "level": level,
    }
    with _log_lock:
        web_logs.append(entry)
    getattr(log, level, log.info)(text)


# ---------------------------------------------------------------------------
# ПОСТОЯННАЯ КНОПКА ТИКЕТОВ
# ---------------------------------------------------------------------------
class PersistentTicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Открыть тикет",
        style=discord.ButtonStyle.primary,
        custom_id="arumi_permanent_v9",
        emoji="📩",
    )
    async def create_ticket(
        self, itn: discord.Interaction, btn: discord.ui.Button
    ) -> None:
        await itn.response.defer(ephemeral=True)

        # Безопасное имя канала: только строчные буквы/цифры/дефис
        safe_name = "".join(
            c if c.isalnum() or c == "-" else "-"
            for c in itn.user.name.lower()
        ).strip("-") or "user"
        channel_name = f"ticket-{safe_name}-{itn.user.id}"

        # Проверяем существующий тикет по точному имени
        if discord.utils.get(itn.guild.text_channels, name=channel_name):
            return await itn.followup.send(
                "У тебя уже есть открытый тикет!", ephemeral=True
            )

        cat = itn.guild.get_channel(TICKET_CATEGORY_ID)
        recruit_role = itn.guild.get_role(1516571905216348170)
        perms = {
            itn.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            itn.user: discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            ),
            itn.guild.me: discord.PermissionOverwrite(
                read_messages=True, manage_channels=True, manage_messages=True
            ),
            recruit_role: discord.PermissionOverwrite(
                read_messages=True, manage_channels=True, manage_messages=True
            ),
        }

        ch = await itn.guild.create_text_channel(
            name=channel_name, category=cat, overwrites=perms
        )

        embed = discord.Embed(
            title="📩 Новый тикет",
            description=(
                f"Привет, {itn.user.mention}! Оставляйте свои заявки по следующей форме, \n"
                "```1. Ваше реальное имя.\n"
                "2. Ваш возраст.\n"
                "3. Почему хотите вступить в семью.```\n\n"
                "Для закрытия тикета используй `/archive`."
            ),
            color=0x5865F2,
            timestamp=datetime.now(),
        )
        embed.set_footer(text=f"ID: {itn.user.id}")
        await ch.send(embed=embed)
        await itn.followup.send(f"Тикет создан: {ch.mention}", ephemeral=True)
        add_web_log(f"Тикет открыт: {ch.name} | {itn.user}")


# ---------------------------------------------------------------------------
# КЛАСС БОТА
# ---------------------------------------------------------------------------
class ArumiBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        self.add_view(PersistentTicketView())
        add_web_log("Persistent views зарегистрированы.")


bot = ArumiBot()


# ---------------------------------------------------------------------------
# ХЕЛПЕР: получить лог-канал
# ---------------------------------------------------------------------------
def get_log_channel() -> discord.TextChannel | None:
    return bot.get_channel(LOG_CHANNEL_ID)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# СОБЫТИЯ СЕРВЕРА
# ---------------------------------------------------------------------------

@bot.event
async def on_ready() -> None:
    await bot.tree.sync()
    add_web_log(f"Бот запущен как {bot.user}. Команды синхронизированы.")


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """Авторолл + лог входа."""
    roles = [
        member.guild.get_role(rid)
        for rid in AUTO_ROLE_IDS
        if member.guild.get_role(rid)
    ]
    if roles:
        try:
            await member.add_roles(*roles, reason="Авторолл при входе")
        except discord.Forbidden:
            add_web_log(f"Нет прав выдать роли для {member}", "warning")

    ch = get_log_channel()
    if ch:
        embed = discord.Embed(
            title="📥 Новый участник",
            description=(
                f"{member.mention} зашёл на сервер.\n"
                f"Аккаунт создан: <t:{int(member.created_at.timestamp())}:R>"
            ),
            color=0x43B581,
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"ID: {member.id}")
        await ch.send(embed=embed)

    add_web_log(f"Вход: {member.name} (ID: {member.id})")


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    """Лог выхода участника."""
    ch = get_log_channel()
    if ch:
        embed = discord.Embed(
            title="📤 Участник покинул сервер",
            description=(
                f"**{discord.utils.escape_markdown(member.name)}** (ID: {member.id})\n"
                f"Был на сервере с: <t:{int(member.joined_at.timestamp())}:R>"
                if member.joined_at else ""
            ),
            color=0xF04747,
            timestamp=datetime.now(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await ch.send(embed=embed)

    add_web_log(f"Выход: {member.name} (ID: {member.id})")


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent) -> None:
    """Лог удалённого сообщения."""
    ch = get_log_channel()
    if not ch:
        return

    embed = discord.Embed(
        title="🗑️ Сообщение удалено",
        color=0xFF0000,
        timestamp=datetime.now(),
    )
    if payload.cached_message:
        msg = payload.cached_message
        if msg.author.bot:
            return
        embed.description = (
            f"**Автор:** {msg.author.mention}\n"
            f"**Канал:** <#{payload.channel_id}>\n"
            f"**Текст:** {msg.content[:1000] or '*(вложение или пусто)*'}"
        )
        embed.set_footer(text=f"ID сообщения: {payload.message_id}")
    else:
        embed.description = (
            f"Сообщение удалено в <#{payload.channel_id}>.\n"
            "*(текст не в кэше)*"
        )

    await ch.send(embed=embed)


@bot.event
async def on_raw_message_edit(payload: discord.RawMessageUpdateEvent) -> None:
    """Лог редактирования сообщения."""
    ch = get_log_channel()
    if not ch:
        return

    cached = payload.cached_message
    if cached and cached.author.bot:
        return

    new_content = (payload.data.get("content") or "")[:1000]
    old_content = (cached.content[:1000] if cached else "*(не в кэше)*")

    # Не логируем если содержимое не изменилось (embed update и т.п.)
    if cached and cached.content == new_content:
        return

    embed = discord.Embed(
        title="✏️ Сообщение отредактировано",
        color=0xFAA61A,
        timestamp=datetime.now(),
    )
    embed.add_field(name="До", value=old_content or "*(пусто)*", inline=False)
    embed.add_field(name="После", value=new_content or "*(пусто)*", inline=False)
    embed.add_field(
        name="Канал",
        value=f"<#{payload.channel_id}>",
        inline=True,
    )
    if cached:
        embed.add_field(name="Автор", value=cached.author.mention, inline=True)
        embed.add_field(
            name="Ссылка",
            value=f"[Перейти](https://discord.com/channels/{payload.guild_id}/{payload.channel_id}/{payload.message_id})",
            inline=True,
        )
    await ch.send(embed=embed)


# ---------------------------------------------------------------------------
# СЛЭШ-КОМАНДЫ
# ---------------------------------------------------------------------------

@bot.tree.command(name="archive", description="Закрыть тикет и сохранить историю")
async def archive(itn: discord.Interaction) -> None:
    if "ticket-" not in itn.channel.name:
        return await itn.response.send_message(
            "❌ Команда работает только в тикетах!", ephemeral=True
        )

    # Проверяем права: либо автор тикета, либо имеет manage_channels
    is_owner = itn.channel.name.endswith(str(itn.user.id))
    has_perm = itn.user.guild_permissions.manage_channels  # type: ignore[union-attr]
    if not (is_owner or has_perm):
        return await itn.response.send_message(
            "❌ Закрыть тикет может только его владелец или модератор.", ephemeral=True
        )

    await itn.response.send_message("📑 Архивация тикета...")

    log_lines: list[str] = []
    async for msg in itn.channel.history(limit=None, oldest_first=True):
        attachments = (
            " | Вложения: " + ", ".join(a.url for a in msg.attachments)
            if msg.attachments else ""
        )
        log_lines.append(
            f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"{msg.author} (ID:{msg.author.id}): {msg.content}{attachments}"
        )

    file_buf = io.BytesIO("\n".join(log_lines).encode("utf-8"))
    archive_ch = bot.get_channel(TICKET_ARCHIVE_ID)
    if archive_ch:
        embed = discord.Embed(
            title="📁 Архив тикета",
            description=(
                f"**Канал:** `{itn.channel.name}`\n"
                f"**Закрыл:** {itn.user.mention}\n"
                f"**Сообщений:** {len(log_lines)}"
            ),
            color=0x747F8D,
            timestamp=datetime.now(),
        )
        await archive_ch.send(  # type: ignore[union-attr]
            embed=embed,
            file=discord.File(file_buf, filename=f"{itn.channel.name}.txt"),
        )

    add_web_log(f"Тикет закрыт: {itn.channel.name} | Закрыл: {itn.user}")
    await itn.channel.delete()


@bot.tree.command(name="setup_tickets", description="Создать панель тикетов в этом канале")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_tickets(itn: discord.Interaction) -> None:
    embed = discord.Embed(
        title="🎫 Поддержка",
        description=(
            "Подавай заявку в семью по кнопке ниже.\n"
        ),
        color=0x5865F2,
    )
    await itn.channel.send(embed=embed, view=PersistentTicketView())
    await itn.response.send_message("✅ Панель тикетов создана.", ephemeral=True)
    add_web_log(f"Панель тикетов создана в #{itn.channel.name}")


@bot.tree.command(name="kick", description="Выгнать участника с сервера")
@app_commands.describe(member="Участник", reason="Причина")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(
    itn: discord.Interaction,
    member: discord.Member,
    reason: str = "Причина не указана",
) -> None:
    if member.top_role >= itn.user.top_role:  # type: ignore[union-attr]
        return await itn.response.send_message(
            "❌ Ты не можешь кикнуть участника с такой же или выше ролью.", ephemeral=True
        )
    await member.kick(reason=f"{itn.user}: {reason}")
    await itn.response.send_message(
        f"✅ {member.mention} выгнан. Причина: {reason}", ephemeral=True
    )
    ch = get_log_channel()
    if ch:
        embed = discord.Embed(
            title="👢 Кик",
            description=f"**Участник:** {member.mention}\n**Причина:** {reason}\n**Модератор:** {itn.user.mention}",
            color=0xFAA61A,
            timestamp=datetime.now(),
        )
        await ch.send(embed=embed)
    add_web_log(f"Кик: {member} | Причина: {reason} | {itn.user}")


@bot.tree.command(name="ban", description="Забанить участника")
@app_commands.describe(member="Участник", reason="Причина", delete_days="Удалить сообщения за N дней (0–7)")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(
    itn: discord.Interaction,
    member: discord.Member,
    reason: str = "Причина не указана",
    delete_days: app_commands.Range[int, 0, 7] = 0,
) -> None:
    if member.top_role >= itn.user.top_role:  # type: ignore[union-attr]
        return await itn.response.send_message(
            "❌ Ты не можешь забанить участника с такой же или выше ролью.", ephemeral=True
        )
    await member.ban(reason=f"{itn.user}: {reason}", delete_message_days=delete_days)
    await itn.response.send_message(
        f"✅ {member.mention} забанен. Причина: {reason}", ephemeral=True
    )
    ch = get_log_channel()
    if ch:
        embed = discord.Embed(
            title="🔨 Бан",
            description=(
                f"**Участник:** {member.mention}\n"
                f"**Причина:** {reason}\n"
                f"**Модератор:** {itn.user.mention}\n"
                f"**Удалено сообщений за:** {delete_days} дн."
            ),
            color=0xFF0000,
            timestamp=datetime.now(),
        )
        await ch.send(embed=embed)
    add_web_log(f"Бан: {member} | Причина: {reason} | {itn.user}", "warning")


@bot.tree.command(name="clear", description="Очистить N сообщений в канале")
@app_commands.describe(amount="Количество сообщений (1–100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(
    itn: discord.Interaction,
    amount: app_commands.Range[int, 1, 100],
) -> None:
    await itn.response.defer(ephemeral=True)
    deleted = await itn.channel.purge(limit=amount)  # type: ignore[union-attr]
    await itn.followup.send(f"✅ Удалено {len(deleted)} сообщений.", ephemeral=True)
    add_web_log(f"Очистка: {len(deleted)} сообщений в #{itn.channel.name} | {itn.user}")


# Обработчик ошибок прав для всех команд
@bot.tree.error
async def on_app_command_error(
    itn: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingPermissions):
        await itn.response.send_message(
            "❌ У тебя нет прав для этой команды.", ephemeral=True
        )
    else:
        add_web_log(f"Ошибка команды: {error}", "error")
        await itn.response.send_message(
            f"❌ Произошла ошибка: `{error}`", ephemeral=True
        )


# ---------------------------------------------------------------------------
# ВЕБ-ДАШБОРД
# ---------------------------------------------------------------------------
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="5">
<title>ArumiBot — Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1117; color: #c9d1d9; font-family: 'Courier New', monospace; padding: 24px; }}
  h1 {{ color: #58a6ff; margin-bottom: 4px; font-size: 1.4rem; }}
  .sub {{ color: #8b949e; font-size: 0.8rem; margin-bottom: 20px; }}
  .log-container {{ max-height: 80vh; overflow-y: auto; border: 1px solid #30363d; border-radius: 8px; padding: 12px; }}
  .entry {{ padding: 4px 0; border-bottom: 1px solid #21262d; font-size: 0.85rem; line-height: 1.5; }}
  .entry:last-child {{ border-bottom: none; }}
  .time {{ color: #8b949e; margin-right: 8px; }}
  .info  {{ color: #58a6ff; }}
  .warning {{ color: #e3b341; }}
  .error {{ color: #f85149; }}
  .empty {{ color: #8b949e; text-align: center; padding: 40px; }}
</style>
</head>
<body>
<h1>⚙️ ArumiBot Engine</h1>
<div class="sub">Автообновление каждые 5 секунд | Последних {count} записей</div>
<div class="log-container">
{entries}
</div>
</body>
</html>"""


app = Flask(__name__)


@app.route("/")
def index() -> str:
    with _log_lock:
        logs_snapshot = list(web_logs)

    if not logs_snapshot:
        entries = '<div class="empty">Нет записей</div>'
    else:
        rows = []
        for e in reversed(logs_snapshot):
            lvl = e.get("level", "info")
            rows.append(
                f'<div class="entry">'
                f'<span class="time">[{e["time"]}]</span>'
                f'<span class="{lvl}">{discord.utils.escape_mentions(e["text"])}</span>'
                f"</div>"
            )
        entries = "\n".join(rows)

    return _HTML_TEMPLATE.format(count=len(logs_snapshot), entries=entries)


def run_web() -> None:
    serve(app, host="0.0.0.0", port=WEB_PORT)


async def console_interface():

    await bot.wait_until_ready()

    print("🖥️ Консоль управления активирована. Введи команду (например, 'send 123456789 Привет!'):")

    

    while not bot.is_closed():

        # Асинхронно ждем ввода из cmd

        command = await aioconsole.ainput(">>> ")

        args = command.split(" ", 2)

        

        if not args or args[0] == "":

            continue

            

        action = args[0].lower()

        

        # Пример: send <ID_КАНАЛА> <ТЕКСТ>

        if action == "send" and len(args) == 3:

            channel_id = int(args[1])

            text = args[2]

            channel = bot.get_channel(channel_id)

            if channel:

                await channel.send(text)

                print(f"✅ Отправлено в {channel.name}")

            else:

                print("❌ Канал не найден.")

                

        # Пример: delete_channel <ID_КАНАЛА>

        elif action == "delete_channel" and len(args) >= 2:

            channel_id = int(args[1])

            channel = bot.get_channel(channel_id)

            if channel:

                await channel.delete()

                print(f"✅ Канал {channel.name} удален.")

            else:

                print("❌ Канал не найден.")

        else:

            print("⚠️ Неизвестная команда или неверный синтаксис.")


# Запускаем консоль вместе со стартом бота

@bot.event

async def on_ready() -> None:

    await bot.tree.sync()

    add_web_log(f"Бот запущен как {bot.user}. Команды синхронизированы.")

    bot.loop.create_task(console_interface()) # Запуск консоли после готовности бота

async def console_interface():
    await bot.wait_until_ready()
    print(f"\n🚀 Бот {bot.user} запущен. Введи 'help' для списка команд.")
    
    while not bot.is_closed():
        try:
            # Читаем ввод из консоли
            command_input = await aioconsole.ainput(">>> ")
            args = command_input.split(" ", 2)
            
            if not args or args[0] == "":
                continue
                
            action = args[0].lower()

            # --- КОМАНДА HELP ---
            if action == "help":
                print("\n" + "="*50)
                print("nuke_server <ID> - Удалить все каналы")
                print("list_guilds      - Список серверов")
                print("exit             - Выход")
                print("="*50 + "\n")

            # --- КОМАНДА СПИСКА СЕРВЕРОВ ---
            elif action == "list_guilds":
                for g in bot.guilds:
                    print(f"ID: {g.id} | Name: {g.name}")

            # --- ТА САМАЯ ЯДЕРНАЯ КНОПКА ---
            elif action == "nuke_server" and len(args) >= 2:
                try:
                    guild_id = int(args[1])
                    guild = bot.get_guild(guild_id)
                    
                    if not guild:
                        print("❌ Сервер с таким ID не найден.")
                        continue

                    # Подтверждение
                    confirm = await aioconsole.ainput(f"⚠️ УДАЛИТЬ ВСЕ каналы на '{guild.name}'? (y/n): ")
                    if confirm.lower() == 'y':
                        print(f"💣 Начинаю аннигиляцию каналов на {guild.name}...")
                        
                        # Удаляем все категории и каналы
                        for channel in guild.channels:
                            try:
                                await channel.delete()
                                print(f"🗑️ Удален: {channel.name}")
                            except Exception as e:
                                print(f"⚠️ Ошибка удаления {channel.name}: {e}")
                        
                        # Создаем чистый канал, чтобы сервер не «сломался»
                        await guild.create_text_channel("main")
                        print(f"✅ Сервер {guild.name} успешно зачищен.")
                    else:
                        print("❌ Отмена операции.")
                except ValueError:
                    print("❌ ID сервера должен быть числом.")

            # --- ВЫХОД ---
            elif action == "exit":
                print("Завершение работы консоли...")
                break

        except Exception as e:
            print(f"⚠️ Произошла ошибка в консоли: {e}")

async def console_interface():
    await bot.wait_until_ready()
    print(f"\n🚀 Консоль ArumiBot активна. Бот: {bot.user}")
    print("Команды:")
    print("1. list_guilds            - Список серверов")
    print("2. clear_roles <ID>       - УДАЛИТЬ ВСЕ РОЛИ")
    print("3. nuke_server <ID>       - Удалить всё (каналы + роли)")
    print("4. send <ID_канала> <текст> - Отправить сообщение")
    print("5. exit                   - Выход\n")

    while not bot.is_closed():
        try:
            command_input = await aioconsole.ainput(">>> ")
            args = command_input.split(" ", 2)
            if not args or args[0] == "": continue
            
            action = args[0].lower()

            # --- СПИСОК СЕРВЕРОВ ---
            if action == "list_guilds":
                for g in bot.guilds:
                    print(f"ID: {g.id} | Название: {g.name}")

            # --- ТОЛЬКО УДАЛЕНИЕ РОЛЕЙ ---
            elif action == "clear_roles" and len(args) >= 2:
                guild = bot.get_guild(int(args[1]))
                if not guild:
                    print("❌ Сервер не найден.")
                    continue

                confirm = await aioconsole.ainput(f"⚠️ Удалить ВСЕ роли на '{guild.name}'? (y/n): ")
                if confirm.lower() == 'y':
                    print(f"🧹 Начинаю удаление ролей на {guild.name}...")
                    count = 0
                    for role in guild.roles:
                        # Пропускаем @everyone, системные роли ботов и роли выше самого бота
                        if role.is_default() or role.managed or role >= guild.me.top_role:
                            continue
                        try:
                            await role.delete(reason="Очистка ролей через консоль")
                            print(f"🗑️ Удалена роль: {role.name}")
                            count += 1
                        except Exception as e:
                            print(f"⚠️ Не удалось удалить {role.name}: {e}")
                    print(f"✅ Готово! Удалено ролей: {count}")
                else:
                    print("❌ Отмена.")

            # --- ПОЛНАЯ ЗАЧИСТКА (КАНАЛЫ + РОЛИ) ---
            elif action == "nuke_server" and len(args) >= 2:
                guild = bot.get_guild(int(args[1]))
                if not guild:
                    print("❌ Сервер не найден.")
                    continue

                confirm = await aioconsole.ainput(f"⚠️ УДАЛИТЬ ВООБЩЕ ВСЁ на '{guild.name}'? (y/n): ")
                if confirm.lower() == 'y':
                    # Удаляем каналы
                    for ch in guild.channels:
                        try: await ch.delete(); print(f"🗑️ Канал удален: {ch.name}")
                        except: pass
                    # Удаляем роли
                    for role in guild.roles:
                        if role.is_default() or role.managed or role >= guild.me.top_role: continue
                        try: await role.delete(); print(f"❌ Роль удалена: {role.name}")
                        except: pass
                    
                    await guild.create_text_channel("main")
                    print(f"✅ Сервер {guild.name} полностью зачищен.")

            elif action == "send" and len(args) == 3:
                channel = bot.get_channel(int(args[1]))
                if channel: 
                    await channel.send(args[2])
                    print("✅ Отправлено.")

            elif action == "exit":
                await bot.close()
                break

        except Exception as e:
            print(f"⚠️ Ошибка: {e}")

# И не забудь запустить это в on_ready
@bot.event
async def on_ready():
    bot.loop.create_task(console_interface())


# ---------------------------------------------------------------------------
# ТОЧКА ВХОДА
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    add_web_log(f"Веб-дашборд запущен на порту {WEB_PORT}.")
    bot.run(TOKEN, log_handler=None)  # logging уже настроен выше