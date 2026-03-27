import discord, asyncio, io, sys
from discord.ext import commands
from datetime import datetime

# Кодировка для Windows
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding='utf-8')
    except: pass

# --- КОНФИГУРАЦИЯ ---
TOKEN = '-'
LOG_CHANNEL_ID = 0000000000000    # канал логов (замените на свой ID)    
AUTO_ROLE_IDS = [000000000000000000]    # автоовыдача ролей (замените на свой ID)
ARCHIVE_ROLE_IDS = [000000000000000000] # роли с доступом к логам тикетов (замените на свой ID)
TICKET_ARCHIVE_ID = 000000000000000000     # канал для архива тикетов (замените на свой ID)
TICKET_CATEGORY_ID = 000000000000000000    # категория для тикетов (замените на свой ID)

# --- ПОСТОЯННЫЙ ИНТЕРФЕЙС ---
class TicketView(discord.ui.View):
    def __init__(self):
        # timeout=None делает кнопку вечной
        super().__init__(timeout=None)

    # custom_id должен быть ВСЕГДА одинаковым, чтобы бот узнал кнопку после перезапуска
    @discord.ui.button(label="Открыть тикет", style=discord.ButtonStyle.primary, custom_id="persistent_tkt_v1", emoji="📩")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        category = guild.get_channel(TICKET_CATEGORY_ID)
        channel_name = f"ticket-{interaction.user.name}".lower()
        
        if discord.utils.get(guild.text_channels, name=channel_name):
            return await interaction.followup.send("У вас уже есть открытый тикет.", ephemeral=True)
            
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, manage_channels=True)
        }
        
        channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)
        await interaction.followup.send(f"Тикет открыт: {channel.mention}", ephemeral=True)
        await channel.send(f"Привет {interaction.user.mention}! Опишите ситуацию. Команды: `!close`, `!archive`")

class ArumiBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all(), help_command=None)

    # Эта функция запускается ОДИН РАЗ при включении бота
    async def setup_hook(self):
        # Регистрируем View, чтобы старые кнопки на сервере снова заработали
        self.add_view(TicketView())

bot = ArumiBot()

# --- ЛОГИРОВАНИЕ ---
async def send_log(embed):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel: await channel.send(embed=embed)

@bot.event
async def on_ready():
    print(f"Бот {bot.user} онлайн. Старые кнопки активированы.")

@bot.event
async def on_raw_message_delete(payload):
    embed = discord.Embed(title="🗑 Сообщение удалено", color=0xFF0000, timestamp=datetime.utcnow())
    embed.add_field(name="Канал", value=f"<#{payload.channel_id}>")
    if payload.cached_message:
        msg = payload.cached_message
        if msg.author.bot: return
        embed.add_field(name="Автор", value=f"{msg.author}")
        embed.description = f"**Текст:**\n{msg.content[:1800]}"
    else:
        embed.description = "*Текст недоступен (сообщения не было в кэше)*"
    await send_log(embed)

@bot.event
async def on_raw_message_edit(payload):
    if 'content' not in payload.data: return
    new_text = payload.data['content']
    before = payload.cached_message
    if before and (before.content == new_text or before.author.bot): return
    embed = discord.Embed(title="✏ Сообщение изменено", color=0x0000FF, timestamp=datetime.utcnow())
    if before:
        embed.add_field(name="Автор", value=str(before.author))
        embed.add_field(name="Было", value=before.content[:1000], inline=False)
    embed.add_field(name="Стало", value=new_text[:1000], inline=False)
    await send_log(embed)

@bot.event
async def on_member_join(member):
    roles = [member.guild.get_role(rid) for rid in AUTO_ROLE_IDS if member.guild.get_role(rid)]
    if roles: await member.add_roles(*roles)
    await send_log(discord.Embed(title="📥 Вход", description=f"{member.mention} зашел на сервер", color=0x00FF00))

# --- КОМАНДЫ ---
@bot.command()
async def archive(ctx):
    if "ticket-" not in ctx.channel.name: return
    if not any(r.id in ARCHIVE_ROLE_IDS for r in ctx.author.roles) and not ctx.author.guild_permissions.administrator:
        return await ctx.send("У вас нет прав.")
    
    await ctx.send("💾 Сохранение архива...")
    history = []
    async for m in ctx.channel.history(limit=None, oldest_first=True):
        history.append(f"[{m.created_at.strftime('%Y-%m-%d %H:%M')}] {m.author}: {m.content}")
    
    buf = io.BytesIO("\n".join(history).encode('utf-8'))
    archive_ch = bot.get_channel(TICKET_ARCHIVE_ID)
    if archive_ch:
        embed = discord.Embed(title="📦 Тикет заархивирован", color=0x2F3136)
        embed.add_field(name="Канал", value=ctx.channel.name)
        embed.add_field(name="Закрыл", value=ctx.author.mention)
        await archive_ch.send(embed=embed, file=discord.File(buf, filename=f"archive-{ctx.channel.name}.txt"))
    await ctx.channel.delete()

@bot.command()
async def close(ctx):
    if "ticket-" in ctx.channel.name: await ctx.channel.delete()

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_tickets(ctx):
    # Эту команду вы используете ОДИН РАЗ, чтобы создать сообщение с кнопкой
    embed = discord.Embed(title="Поддержка", description="Нажмите на кнопку ниже, чтобы создать тикет.", color=0x00FF00)
    await ctx.send(embed=embed, view=TicketView())

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, n: int = 10):
    await ctx.channel.purge(limit=n + 1)

bot.run(TOKEN)