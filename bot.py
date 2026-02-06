# aura_admin_review_bot.py
# ✅ วางทับทั้งไฟล์ได้เลย
# เป้าหมาย: เอา "ใช้แล้ว /rate" ออก -> เลิกใช้ Slash command (/rate) แล้วใช้ Prefix command (!rate) แทน
# NOTE: Discord ไม่สามารถซ่อน "ใช้แล้ว /rate" ได้ถ้ายังใช้ Slash command

import os
from dataclasses import dataclass
from typing import Optional, Dict, Tuple

import discord
from discord.ext import commands
from dotenv import load_dotenv
from supabase import create_client, Client

# =========================
# ENV
# =========================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN")
if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# =========================
# BOT
# =========================
intents = discord.Intents.default()
intents.message_content = True  # ✅ ใช้ !rate / !adminscore
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# CONFIG
# =========================
CATEGORIES = {
    "service": "การบริการ",
    "solving": "การแก้ไขปัญหา",
    "communication": "การพูดคุย",
}

@dataclass
class RatingDraft:
    service: Optional[int] = None
    solving: Optional[int] = None
    communication: Optional[int] = None

# (admin_id, rater_id) -> RatingDraft
drafts: Dict[Tuple[int, int], RatingDraft] = {}

# =========================
# SUPABASE HELPERS
# =========================
def ensure_admin(admin_id: int):
    sb.table("admins").upsert({"admin_id": str(admin_id)}).execute()

def set_admin_image(admin_id: int, image_url: str):
    sb.table("admins").upsert({"admin_id": str(admin_id), "custom_image": image_url}).execute()

def get_custom_image(admin_id: int) -> Optional[str]:
    try:
        res = sb.table("admins").select("custom_image").eq("admin_id", str(admin_id)).execute()
        if res.data and res.data[0].get("custom_image"):
            return res.data[0]["custom_image"]
    except Exception:
        pass
    return None

def upsert_rating(admin_id: int, rater_id: int, service: int, solving: int, communication: int):
    sb.table("ratings").upsert(
        {
            "admin_id": str(admin_id),
            "rater_id": str(rater_id),
            "service": service,
            "solving": solving,
            "communication": communication,
        },
        on_conflict="admin_id,rater_id"
    ).execute()

def fetch_stats(admin_id: int):
    res = sb.table("ratings").select("service,solving,communication").eq("admin_id", str(admin_id)).execute()
    rows = res.data or []

    if not rows:
        return {
            "voters": 0,
            "avg_service": 0.0,
            "avg_solving": 0.0,
            "avg_communication": 0.0,
            "avg_total": 0.0,
        }

    n = len(rows)
    s = sum(r["service"] for r in rows) / n
    so = sum(r["solving"] for r in rows) / n
    c = sum(r["communication"] for r in rows) / n

    return {
        "voters": n,
        "avg_service": float(s),
        "avg_solving": float(so),
        "avg_communication": float(c),
        "avg_total": float((s + so + c) / 3),
    }

# =========================
# UI HELPERS
# =========================
def stars(v: float) -> str:
    n = int(round(v))
    n = max(0, min(5, n))
    return "⭐" * n if n > 0 else "—"

async def resolve_admin_display(guild: Optional[discord.Guild], admin_id: int) -> Tuple[str, Optional[str]]:
    if guild:
        m = guild.get_member(admin_id)
        if m:
            return m.display_name, str(m.display_avatar.url)
        try:
            m2 = await guild.fetch_member(admin_id)
            return m2.display_name, str(m2.display_avatar.url)
        except Exception:
            pass

    try:
        u = await bot.fetch_user(admin_id)
        name = u.name
        avatar = str(u.display_avatar.url) if u.display_avatar else None
        return name, avatar
    except Exception:
        return f"User {admin_id}", None

def make_embed_for_admin(admin_name: str, thumb_url: Optional[str], stats: dict) -> discord.Embed:
    e = discord.Embed(
        title=f"🌟 Admin Review — {admin_name}",
        description="ให้ดาว 3 หมวด ระบบคำนวณค่าเฉลี่ยให้อัตโนมัติ",
        color=0x64C3F1
    )

    if thumb_url:
        e.set_thumbnail(url=thumb_url)

    e.add_field(
        name="คะแนนรวม",
        value=f"**{stats['avg_total']:.2f}** / 5 {stars(stats['avg_total'])}",
        inline=False
    )
    e.add_field(name="การบริการ", value=f"{stats['avg_service']:.2f} / 5", inline=True)
    e.add_field(name="การแก้ไขปัญหา", value=f"{stats['avg_solving']:.2f} / 5", inline=True)
    e.add_field(name="การพูดคุย", value=f"{stats['avg_communication']:.2f} / 5", inline=True)
    e.set_footer(text=f"ผู้โหวตทั้งหมด: {stats['voters']} คน | AURA CITY")
    return e

async def make_embed(admin_id: int, guild: Optional[discord.Guild]) -> discord.Embed:
    stats = fetch_stats(admin_id)
    name, discord_avatar = await resolve_admin_display(guild, admin_id)

    custom = get_custom_image(admin_id)
    thumb = custom or discord_avatar

    return make_embed_for_admin(name, thumb, stats)

# =========================
# UI COMPONENTS
# =========================
class CategorySelect(discord.ui.Select):
    def __init__(self, admin_id: int):
        self.admin_id = admin_id
        super().__init__(
            placeholder="เลือกหมวดที่จะให้ดาว",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=CATEGORIES[k], value=k) for k in CATEGORIES]
        )

    async def callback(self, interaction: discord.Interaction):
        # ✅ กัน interaction fail
        await interaction.response.defer(ephemeral=True, thinking=False)

        key = (self.admin_id, interaction.user.id)
        drafts[key] = drafts.get(key) or RatingDraft()

        cat = self.values[0]
        await interaction.followup.send(
            f"เลือกจำนวนดาวสำหรับ **{CATEGORIES[cat]}** (1–5)",
            view=StarSelectView(self.admin_id, cat),
            ephemeral=True
        )

class StarSelect(discord.ui.Select):
    def __init__(self, admin_id: int, category: str):
        self.admin_id = admin_id
        self.category = category
        super().__init__(
            placeholder="เลือกดาว (1–5)",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=f"{i} ดาว", value=str(i)) for i in range(1, 6)]
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if interaction.user.id == self.admin_id:
            return await interaction.followup.send("❌ ให้ดาวตัวเองไม่ได้นะ", ephemeral=True)

        key = (self.admin_id, interaction.user.id)
        draft = drafts.get(key) or RatingDraft()
        drafts[key] = draft

        score = int(self.values[0])
        setattr(draft, self.category, score)

        if None in draft.__dict__.values():
            return await interaction.followup.send("บันทึกแล้ว ✅ เลือกหมวดต่อได้เลย", ephemeral=True)

        try:
            upsert_rating(self.admin_id, interaction.user.id, draft.service, draft.solving, draft.communication)
            drafts.pop(key, None)
            return await interaction.followup.send("🎉 ส่งคะแนนครบแล้ว ขอบคุณมาก!", ephemeral=True)
        except Exception as e:
            return await interaction.followup.send(f"❌ บันทึกไม่สำเร็จ: {e}", ephemeral=True)

class ReviewView(discord.ui.View):
    def __init__(self, admin_id: int):
        super().__init__(timeout=None)
        self.admin_id = admin_id
        self.add_item(CategorySelect(admin_id))

    @discord.ui.button(label="รีเฟรชคะแนน", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()  # ✅ กัน 10062
        try:
            embed = await make_embed(self.admin_id, interaction.guild)
            await interaction.message.edit(embed=embed, view=self)
        except Exception as e:
            try:
                await interaction.followup.send(f"❌ รีเฟรชไม่สำเร็จ: {e}", ephemeral=True)
            except Exception:
                pass

class StarSelectView(discord.ui.View):
    def __init__(self, admin_id: int, category: str):
        super().__init__(timeout=60)
        self.add_item(StarSelect(admin_id, category))

# =========================
# COMMANDS (PREFIX) ✅ ไม่มี "ใช้แล้ว /rate"
# =========================
@bot.command(name="rate")
async def rate_cmd(ctx: commands.Context, admin: discord.Member):
    # ลบข้อความคำสั่งเพื่อลดรก (ต้องมีสิทธิ์ Manage Messages)
    try:
        await ctx.message.delete()
    except Exception:
        pass

    image: Optional[discord.Attachment] = ctx.message.attachments[0] if ctx.message.attachments else None

    try:
        ensure_admin(admin.id)

        if image:
            if image.content_type and not image.content_type.startswith("image/"):
                return await ctx.send("แนบได้เฉพาะไฟล์รูปเท่านั้นนะ 🖼️", delete_after=8)
            set_admin_image(admin.id, image.url)

        embed = await make_embed(admin.id, ctx.guild)
        await ctx.send(embed=embed, view=ReviewView(admin.id))

    except Exception as e:
        await ctx.send(f"❌ ทำรายการไม่สำเร็จ: {e}")

@bot.command(name="adminscore")
async def adminscore_cmd(ctx: commands.Context, admin: discord.Member):
    try:
        await ctx.message.delete()
    except Exception:
        pass

    try:
        embed = await make_embed(admin.id, ctx.guild)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"❌ ทำรายการไม่สำเร็จ: {e}")

# =========================
# READY
# =========================
@bot.event
async def on_ready():
    print(f"✅ Bot Online: {bot.user}")

# =========================
# RUN
# =========================
bot.run(DISCORD_TOKEN)
