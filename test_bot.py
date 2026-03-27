import discord, asyncio, io, sys, os, threading
from discord.ext import commands
from discord import app_commands
from datetime import datetime
from flask import Flask
from waitress import serve

# --- ИСПРАВЛЕНИЕ КОДИРОВКИ ДЛЯ ВИНДОУС ---
# Это уберет визуальную разницу между VS Code и Батником
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding='utf-8')
    except: pass

# --- КОНФИГУРАЦИЯ ---
TOKEN = '-'
LOG_CHANNEL_ID = 000000000000000       # Логи действий (удаления, входы)
TICKET_ARCHIVE_ID = 00000000000000     # Канал для .txt файлов
TICKET_CATEGORY_ID = 000000000000000000000000    # Где создаются тикеты
AUTO_ROLE_IDS = [000000000000000000000]

# --- ЛОГИ ДЛЯ САЙТА (ТЕХНИЧЕСКИЕ) ---
web_logs = []
def add_web_log(text):
    t = datetime.now().strftime('%H:%M:%S')
    msg = f"[{t}] {text}"
    print(msg)
    web_logs.append(msg)
    if len(web_logs) > 30: web_logs.pop(0)

# --- ВЬЮШКА ТИКЕТОВ (ПЕРСИСТЕНТНАЯ) ---
class PersistentTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Открыть тикет", style=discord.ButtonStyle.primary, custom_id="arumi_final_fix_v9", emoji="📩")
    async def create_ticket(self, itn: discord.Interaction, btn: discord.ui.Button):
        await itn.response.defer(ephemeral=True)
        name = f"ticket-{itn.user.name}".lower().replace(" ", "-")
        
        if discord.utils.get(itn.guild.text_channels, name=name):
            return await itn.followup.send("У вас уже есть открытый тикет!", ephemeral=True)
        
        cat = itn.guild.get_channel(TICKET_CATEGORY_ID)
        perms = {
            itn.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            itn.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            itn.guild.me: discord.PermissionOverwrite(read_messages=True, manage_channels=True)
        }
        
        ch = await itn.guild.create_text_channel(name=name, category=cat, overwrites=perms)
        await itn.followup.send(f"Тикет создан: {ch.mention}", ephemeral=True)
        
        em = discord.Embed(title="Поддержка Arumi", description="Опишите ваш вопрос.\nДля завершения используйте `/archive`.", color=0x3498db)
        await ch.send(content=itn.user.mention, embed=em)
        add_web_log(f"Тикет {ch.name} открыт пользователем {itn.user}")

# --- ОСНОВНОЙ БОТ ---
class ArumiBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())

    async def setup_hook(self):
        self.add_view(PersistentTicketView()) # Регистрация кнопки
        add_web_log("Система тикетов (PersistentView) готова.")

bot = ArumiBot()

# --- ЛОГИ СЕРВЕРА В DISCORD ---

@bot.event
async def on_raw_message_delete(p):
    log_ch = bot.get_channel(LOG_CHANNEL_ID)
    if not log_ch: return
    em = discord.Embed(title="🗑 Сообщение удалено", color=0xe74c3c, timestamp=datetime.now())
    if p.cached_message:
        if p.cached_message.author.bot: return
        em.description = f"**Автор:** {p.cached_message.author.mention}\n**Канал:** <#{p.channel_id}>\n**Текст:** {p.cached_message.content[:1000]}"
    else:
        em.description = f"Удалено сообщение в <#{p.channel_id}> (текст не закэширован)."
    await log_ch.send(embed=em)

@bot.event
async def on_member_join(m):
    # Роли
    rls = [m.guild.get_role(rid) for rid in AUTO_ROLE_IDS if m.guild.get_role(rid)]
    if rls: await m.add_roles(*rls)
    # Лог в ДС
    log_ch = bot.get_channel(LOG_CHANNEL_ID)
    if log_ch:
        em = discord.Embed(title="📥 Вход на сервер", description=f"{m.mention} присоединился. Роли выданы.", color=0x2ecc71)
        await log_ch.send(embed=em)
    add_web_log(f"Новый участник: {m.name}")

# --- СЛЭШ-КОМАНДЫ ---

@bot.tree.command(name="archive", description="Сохранить лог и закрыть тикет")
async def archive(itn: discord.Interaction):
    if "ticket-" not in itn.channel.name:
        return await itn.response.send_message("Только для тикетов!", ephemeral=True)
    
    await itn.response.send_message("📑 Архивирую...")
    
    history = []
    async for msg in itn.channel.history(limit=None, oldest_first=True):
        ts = msg.created_at.strftime('%H:%M')
        history.append(f"[{ts}] {msg.author}: {msg.content}")
    
    f = io.BytesIO("\n".join(history).encode('utf-8'))
    arch_ch = bot.get_channel(TICKET_ARCHIVE_ID)
    if arch_ch:
        await arch_ch.send(content=f"📁 Архив тикета `{itn.channel.name}`", file=discord.File(f, filename=f"{itn.channel.name}.txt"))
    
    add_web_log(f"Архив {itn.channel.name} успешно создан.")
    await itn.channel.delete()

@bot.tree.command(name="setup_tickets", description="Создать панель тикетов")
@app_commands.checks.has_permissions(administrator=True)
async def setup(itn: discord.Interaction):
    em = discord.Embed(title="Центр Поддержки", description="Нажмите 📩, чтобы связаться с администрацией.", color=0x2ecc71)
    await itn.channel.send(embed=em, view=PersistentTicketView())
    await itn.response.send_message("Панель создана!", ephemeral=True)

@bot.command()
async def sync(ctx):
    await bot.tree.sync()
    await ctx.send("✅ Слэш-команды синхронизированы!")

@bot.event
async def on_ready():
    add_web_log(f"Бот {bot.user} онлайн. Порт сайта: 5000")

# --- ВЕБ-ИНТЕРФЕЙС ---
app = Flask(__name__)
@app.route('/')
def home():
    l = "<br>".join(web_logs)
    return f"<html><head><meta charset='utf-8'></head><body style='background:#121212;color:#00ff00;font-family:monospace;padding:20px;'><h2>Arumi System Engine</h2><hr>{l}</body><script>setTimeout(()=>{{location.reload();}},4000);</script></html>"

if __name__ == "__main__":
    threading.Thread(target=lambda: serve(app, host='0.0.0.0', port=5000), daemon=True).start()
    bot.run(TOKEN)
