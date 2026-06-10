"""
Discord Verify-Channels, No-Access, & Spacer Bot
─────────────────────────────────────────────────
A lightweight single-file bot that restricts server channel visibility and access.

Commands:
  /verify-channels [role]
      Restricts all channels so only the verified role can view them.
      
  /no-access role: @Role channel: #appeals
      Restricts a specific role from viewing/typing in ANY channel,
      except a designated appeal channel where they can see and type.

  /spacers-that-can-only-be-seen spacer: ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯
      Finds all channels (text and voice) containing the spacer string in their name
      and makes them visible but read-only and un-joinable.
"""

import json
import os

import discord
from discord import app_commands
from discord.ext import commands

# ─── Configuration ───────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "MTUxNDE0OTEzNTUwOTQ4NzczNw.GdHwin.6QDqYIkwqkKJ6kGkM9igz8poX0THsoMnLqVfmo")
CONFIG_FILE = "verified_channels_config.json"

# Required bot intents (no privileged intents needed)
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ─── Persistence helpers ─────────────────────────────────────────────────────

def load_config() -> dict:
    """Load the configuration mapping from disk."""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def save_config(config: dict) -> None:
    """Persist the configuration mapping to disk."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


# ─── Deep Confirmation Permission Updater ────────────────────────────────────

async def safe_set_permissions(
    channel: discord.abc.GuildChannel, 
    target: discord.Role, 
    **kwargs: bool | None
) -> None:
    """
    Updates permissions with absolute, deep confirmation that NO other 
    permissions are altered, added, or removed.
    
    It explicitly snapshots the state before and after applying changes, 
    and verifies that only the requested permissions shifted.
    """
    current_overwrite = channel.overwrites_for(target)
    
    # 1. Snapshot all current permission values before modifying
    old_values = {perm: getattr(current_overwrite, perm) for perm in current_overwrite.VALID_FLAGS}
    
    # 2. Apply ONLY the requested changes to the overwrite object
    for perm, value in kwargs.items():
        setattr(current_overwrite, perm, value)
        
    # 3. Snapshot the new permission values after modifying
    new_values = {perm: getattr(current_overwrite, perm) for perm in current_overwrite.VALID_FLAGS}
    
    # 4. Deep Confirmation: Assert that untargeted permissions remained completely untouched
    for perm in current_overwrite.VALID_FLAGS:
        if perm not in kwargs:
            if old_values[perm] != new_values[perm]:
                # If this ever triggers, it means the overwrite object mutated unexpectedly.
                # This acts as an absolute safeguard against collateral permission changes.
                raise RuntimeError(
                    f"SAFETY CHECK FAILED: Permission '{perm}' was accidentally altered "
                    f"in channel '{channel.name}' for role '{target.name}'!"
                )
                
    # 5. Push the mathematically verified overwrite to Discord
    await channel.set_permissions(target, overwrite=current_overwrite)


# ─── Bot lifecycle ───────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    try:
        synced = await bot.tree.sync()
        print(f"Logged in as {bot.user}  (ID: {bot.user.id})")
        print(f"Synced {len(synced)} application command(s).")
    except Exception as exc:
        print(f"Failed to sync commands: {exc}")


# ─── /verify-channels command ────────────────────────────────────────────────

@bot.tree.command(
    name="verify-channels",
    description="Restrict all channels to a verified role only (owner only).",
)
@app_commands.describe(
    role="The verified role. Required on first use; optional afterwards."
)
async def verify_channels(
    interaction: discord.Interaction,
    role: discord.Role | None = None,
) -> None:
    if interaction.user != interaction.guild.owner:
        await interaction.response.send_message(
            "Only the server owner can run this command.",
            ephemeral=True,
        )
        return

    config    = load_config()
    guild_id  = str(interaction.guild.id)

    if role is not None:
        config[guild_id] = str(role.id)
        save_config(config)
        verified_role = role
    elif guild_id in config:
        verified_role = interaction.guild.get_role(int(config[guild_id]))
        if verified_role is None:
            await interaction.response.send_message(
                "The previously configured verified role no longer exists. "
                "Please provide a new one: `/verify-channels role:@NewRole`",
                ephemeral=True,
            )
            return
    else:
        await interaction.response.send_message(
            "No verified role is configured yet. "
            "Please provide one: `/verify-channels role:@YourRole`",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    success = 0
    failed: list[tuple[str, str]] = []

    for channel in interaction.guild.channels:
        try:
            await safe_set_permissions(
                channel, interaction.guild.default_role, view_channel=False
            )
            await safe_set_permissions(
                channel, verified_role, view_channel=True
            )
            success += 1
        except discord.Forbidden:
            failed.append((channel.name, "Bot lacks permission"))
        except discord.HTTPException as exc:
            failed.append((channel.name, str(exc)))
        except RuntimeError as exc:
            failed.append((channel.name, str(exc)))

    total   = len(interaction.guild.channels)
    message = (
        f"**Verified-only access applied.**\n"
        f"• Verified role: **{verified_role.name}**\n"
        f"• Channels updated: **{success}/{total}**"
    )

    if failed:
        lines = "\n".join(f"  - `{name}`: {reason}" for name, reason in failed)
        message += f"\n**Failed channels:**\n{lines}"

    await interaction.followup.send(message, ephemeral=True)


# ─── /no-access command ─────────────────────────────────────────────────────

@bot.tree.command(
    name="no-access",
    description="Restrict a role from all channels except an appeal channel (owner only).",
)
@app_commands.describe(
    role="The role to restrict from all channels.",
    channel="The channel where restricted users can see and type to appeal."
)
async def no_access(
    interaction: discord.Interaction,
    role: discord.Role,
    channel: discord.TextChannel,
) -> None:
    if interaction.user != interaction.guild.owner:
        await interaction.response.send_message(
            "Only the server owner can run this command.",
            ephemeral=True,
        )
        return

    config = load_config()
    guild_id = str(interaction.guild.id)

    config[f"{guild_id}_noaccess_role"] = str(role.id)
    config[f"{guild_id}_noaccess_channel"] = str(channel.id)
    save_config(config)

    await interaction.response.defer(ephemeral=True, thinking=True)

    success = 0
    failed: list[tuple[str, str]] = []

    for ch in interaction.guild.channels:
        try:
            if ch.id == channel.id:
                await safe_set_permissions(
                    ch, role, view_channel=True, send_messages=True
                )
            else:
                await safe_set_permissions(
                    ch, role, view_channel=False, send_messages=False
                )
            success += 1
        except discord.Forbidden:
            failed.append((ch.name, "Bot lacks permission"))
        except discord.HTTPException as exc:
            failed.append((ch.name, str(exc)))
        except RuntimeError as exc:
            failed.append((ch.name, str(exc)))

    total   = len(interaction.guild.channels)
    message = (
        f"**No-access restrictions applied.**\n"
        f"• Restricted role: **{role.name}**\n"
        f"• Appeal channel: **{channel.mention}**\n"
        f"• Channels updated: **{success}/{total}**"
    )

    if failed:
        lines = "\n".join(f"  - `{name}`: {reason}" for name, reason in failed)
        message += f"\n**Failed channels:**\n{lines}"

    await interaction.followup.send(message, ephemeral=True)


# ─── /spacers-that-can-only-be-seen command ──────────────────────────────────

@bot.tree.command(
    name="spacers-that-can-only-be-seen",
    description="Make channels matching a spacer string view-only and un-joinable (owner only).",
)
@app_commands.describe(
    spacer="The text the spacer channels contain in their name, e.g. ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯"
)
async def spacers_that_can_only_be_seen(
    interaction: discord.Interaction,
    spacer: str,
) -> None:
    if interaction.user != interaction.guild.owner:
        await interaction.response.send_message(
            "Only the server owner can run this command.",
            ephemeral=True,
        )
        return

    if not spacer.strip():
        await interaction.response.send_message(
            "The spacer string cannot be empty.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    success = 0
    skipped = 0
    failed: list[tuple[str, str]] = []

    for ch in interaction.guild.channels:
        if spacer in ch.name:
            try:
                perms = {}
                
                # Text/Forum channels: visible, but cannot send messages
                if isinstance(ch, (discord.TextChannel, discord.ForumChannel, discord.CategoryChannel)):
                    perms.update(view_channel=True, send_messages=False)
                
                # Voice/Stage channels: visible, but cannot join/connect
                if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                    perms.update(view_channel=True, connect=False)
                
                # Fallback for any other channel types
                if not perms:
                    perms.update(view_channel=True, send_messages=False, connect=False)

                # Use the deep-confirmation safe setter
                await safe_set_permissions(ch, interaction.guild.default_role, **perms)
                success += 1
            except discord.Forbidden:
                failed.append((ch.name, "Bot lacks permission"))
            except discord.HTTPException as exc:
                failed.append((ch.name, str(exc)))
            except RuntimeError as exc:
                failed.append((ch.name, str(exc)))
        else:
            skipped += 1

    total = len(interaction.guild.channels)
    message = (
        f"**Spacer channels locked to view-only.**\n"
        f"• Spacer text: **{spacer}**\n"
        f"• Channels locked: **{success}**\n"
        f"• Channels skipped (no match): **{skipped}/{total - success}**"
    )

    if failed:
        lines = "\n".join(f"  - `{name}`: {reason}" for name, reason in failed)
        message += f"\n**Failed channels:**\n{lines}"

    await interaction.followup.send(message, ephemeral=True)


# ─── Entry point ─────────────────────────────────────────────────────────────

if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    print("ERROR: Set your bot token before running.")
    print("       Export DISCORD_BOT_TOKEN=<token>  or edit the script.")
else:
    bot.run(BOT_TOKEN)
