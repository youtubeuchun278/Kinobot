"""Admin panel: kino, kanal, admin, narx, statistika, xabar va reklama boshqaruvi."""
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
import database as db
import keyboards as kb
import states as st
from utils import (
    channel_ref_from_link,
    format_admin_row,
    format_user_row,
    is_valid_code,
)


def _level(owner_id: int, user_id: int):
    return db.is_admin(owner_id, user_id)


async def _require_admin(event, owner_id: int) -> bool:
    user_id = event.from_user.id
    if _level(owner_id, user_id) is None:
        if isinstance(event, CallbackQuery):
            await event.answer("⛔️ Sizda ruxsat yo'q.", show_alert=True)
        else:
            await event.answer("⛔️ Sizda ruxsat yo'q.")
        return False
    return True


async def _require_admin_manager(event, owner_id: int) -> bool:
    user_id = event.from_user.id
    level = _level(owner_id, user_id)
    if level not in ("ASOSIY", "SUB-ADMIN"):
        if isinstance(event, CallbackQuery):
            await event.answer("⛔️ Faqat ASOSIY yoki SUB-ADMIN admin qo'sha/tahrirlay oladi.", show_alert=True)
        else:
            await event.answer("⛔️ Faqat ASOSIY yoki SUB-ADMIN admin qo'sha/tahrirlay oladi.")
        return False
    return True


# ---------------------------------------------------------------- entry
async def cmd_admin(message: Message, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(message, owner_id):
        return
    await state.clear()
    await message.answer("🛠 <b>Admin panel</b>", reply_markup=kb.admin_menu_kb())


async def show_admin_menu(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    await state.clear()
    await callback.answer()
    try:
        await callback.message.edit_text("🛠 <b>Admin panel</b>", reply_markup=kb.admin_menu_kb())
    except TelegramBadRequest:
        await callback.message.answer("🛠 <b>Admin panel</b>", reply_markup=kb.admin_menu_kb())


# ============================================================ 1. KINO QO'SHISH
async def add_movie_start(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    await callback.answer()
    await state.set_state(st.AddMovie.code)
    await callback.message.answer(
        "🎬 Kino kodini kiriting (2-4 xonali raqam, masalan 124):", reply_markup=kb.cancel_kb()
    )


async def add_movie_code(message: Message, state: FSMContext, owner_id: int) -> None:
    code = (message.text or "").strip()
    if not is_valid_code(code):
        await message.answer("❗️ Kod noto'g'ri. Faqat 2-4 xonali raqam bo'lishi kerak:")
        return
    if db.get_movie(owner_id, code) is not None:
        await message.answer("❗️ Bu kod band. Boshqa kod kiriting:")
        return
    await state.update_data(code=code)
    await state.set_state(st.AddMovie.title)
    await message.answer("📝 Kino nomini kiriting:")


async def add_movie_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=(message.text or "").strip())
    await state.set_state(st.AddMovie.description)
    await message.answer("📄 Kino tavsifini kiriting (yo'q bo'lsa \"-\" yozing):")


async def add_movie_description(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    await state.update_data(description="" if text == "-" else text)
    await state.set_state(st.AddMovie.video)
    await message.answer("🎥 Kino videosini (mp4 fayl) yuboring:")


async def add_movie_video(message: Message, state: FSMContext) -> None:
    file_id = None
    if message.video:
        file_id = message.video.file_id
    elif message.document:
        file_id = message.document.file_id
    if not file_id:
        await message.answer("❗️ Iltimos, video fayl yuboring:")
        return
    await state.update_data(video_file_id=file_id)
    await state.set_state(st.AddMovie.poster)
    await message.answer("🖼 Kino banerini (rasm) yuboring (yo'q bo'lsa \"-\" yozing):")


async def add_movie_poster(message: Message, state: FSMContext) -> None:
    if message.text and message.text.strip() == "-":
        await state.update_data(poster_file_id=None)
    elif message.photo:
        await state.update_data(poster_file_id=message.photo[-1].file_id)
    else:
        await message.answer("❗️ Rasm yuboring yoki bannersiz o'tish uchun \"-\" yozing:")
        return
    await state.set_state(st.AddMovie.tariff)
    await message.answer("💳 Kino tarifini tanlang:", reply_markup=kb.tariff_choice_kb("newmovie_tariff"))


async def add_movie_tariff(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    tariff = callback.data.split(":")[1]
    data = await state.get_data()
    ok = db.add_movie(
        owner_id,
        data["code"],
        data["title"],
        data.get("description", ""),
        data["video_file_id"],
        data.get("poster_file_id"),
        tariff,
    )
    await state.clear()
    await callback.answer()
    if ok:
        await callback.message.answer(
            f"✅ Kino qo'shildi!\nKod: {data['code']}\nNomi: {data['title']}\nTarif: {tariff}",
            reply_markup=kb.back_to_admin_kb(),
        )
    else:
        await callback.message.answer("❗️ Xatolik: bu kod band bo'lib qoldi.", reply_markup=kb.back_to_admin_kb())


# ============================================================ 2. KINO TAHRIRLASH
async def edit_movie_start(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    await callback.answer()
    await state.set_state(st.EditMovie.waiting_code)
    await callback.message.answer("✏️ Tahrirlamoqchi bo'lgan kino kodini kiriting:", reply_markup=kb.cancel_kb())


async def edit_movie_code(message: Message, state: FSMContext, owner_id: int) -> None:
    code = (message.text or "").strip()
    movie = db.get_movie(owner_id, code)
    if movie is None:
        await message.answer("❗️ Bunday kodli kino topilmadi. Qaytadan kiriting:")
        return
    await state.update_data(code=code)
    await state.set_state(st.EditMovie.choosing_field)
    text = (
        f"🎬 <b>{movie['title']}</b>\nKod: {movie['code']}\nTarif: {movie['tariff']}\n\n"
        f"{movie['description'] or ''}\n\nNimani tahrirlaysiz?"
    )
    await message.answer(text, reply_markup=kb.movie_edit_fields_kb())


async def edit_movie_choose_field(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    field = callback.data.split(":")[1]
    data = await state.get_data()
    code = data["code"]
    await callback.answer()

    if field == "delete":
        db.delete_movie(owner_id, code)
        await state.clear()
        await callback.message.answer("🗑 Kino o'chirildi.", reply_markup=kb.back_to_admin_kb())
        return

    if field == "tariff":
        await state.update_data(field=field)
        await state.set_state(st.EditMovie.new_value)
        await callback.message.answer("Yangi tarifni tanlang:", reply_markup=kb.tariff_choice_kb("editmovie_tariff"))
        return

    await state.update_data(field=field)
    await state.set_state(st.EditMovie.new_value)
    prompts = {
        "code": "Yangi kodni kiriting (2-4 xonali raqam):",
        "title": "Yangi nomni kiriting:",
        "description": "Yangi tavsifni kiriting:",
        "video_file_id": "Yangi videoni yuboring:",
        "poster_file_id": "Yangi bannerni (rasm) yuboring:",
    }
    await callback.message.answer(prompts[field])


async def edit_movie_tariff_value(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    tariff = callback.data.split(":")[1]
    data = await state.get_data()
    db.update_movie_field(owner_id, data["code"], "tariff", tariff)
    await state.clear()
    await callback.answer("Yangilandi")
    await callback.message.answer(f"✅ Tarif {tariff} ga o'zgartirildi.", reply_markup=kb.back_to_admin_kb())


async def edit_movie_new_value(message: Message, state: FSMContext, owner_id: int) -> None:
    data = await state.get_data()
    field = data["field"]
    code = data["code"]

    if field == "code":
        new_code = (message.text or "").strip()
        if not is_valid_code(new_code):
            await message.answer("❗️ Kod noto'g'ri. Qaytadan kiriting:")
            return
        if db.get_movie(owner_id, new_code) is not None:
            await message.answer("❗️ Bu kod band. Boshqa kod kiriting:")
            return
        db.update_movie_field(owner_id, code, "code", new_code)
    elif field == "title":
        db.update_movie_field(owner_id, code, "title", (message.text or "").strip())
    elif field == "description":
        db.update_movie_field(owner_id, code, "description", (message.text or "").strip())
    elif field == "video_file_id":
        file_id = message.video.file_id if message.video else (message.document.file_id if message.document else None)
        if not file_id:
            await message.answer("❗️ Video yuboring:")
            return
        db.update_movie_field(owner_id, code, "video_file_id", file_id)
    elif field == "poster_file_id":
        if not message.photo:
            await message.answer("❗️ Rasm yuboring:")
            return
        db.update_movie_field(owner_id, code, "poster_file_id", message.photo[-1].file_id)

    await state.clear()
    await message.answer("✅ Kino yangilandi.", reply_markup=kb.back_to_admin_kb())


# ============================================================ 3. BALANS TO'LDIRISH
async def topup_start(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    await callback.answer()
    await state.set_state(st.TopUpBalance.waiting_user_id)
    await callback.message.answer("💰 Foydalanuvchi ID raqamini kiriting:", reply_markup=kb.cancel_kb())


async def topup_user_id(message: Message, state: FSMContext, owner_id: int) -> None:
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("❗️ Faqat raqam kiriting:")
        return
    user = db.get_user(owner_id, user_id)
    if user is None:
        await message.answer("❗️ Bunday foydalanuvchi botda ro'yxatdan o'tmagan.")
        return
    await state.update_data(user_id=user_id)
    await state.set_state(st.TopUpBalance.waiting_amount)
    await message.answer(f"💵 {user_id} ga qancha so'm qo'shamiz? (raqam kiriting)")


async def topup_amount(message: Message, state: FSMContext, owner_id: int, bot: Bot) -> None:
    try:
        amount = int((message.text or "").strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗️ Musbat raqam kiriting:")
        return
    data = await state.get_data()
    user_id = data["user_id"]
    db.add_balance(owner_id, user_id, amount)
    db.add_transaction(user_id, owner_id, amount, "TOPUP", "Admin tomonidan to'ldirildi")
    await state.clear()
    await message.answer(f"✅ {user_id} balansiga {amount} so'm qo'shildi.", reply_markup=kb.back_to_admin_kb())
    try:
        await bot.send_message(user_id, f"💰 Hisobingiz {amount} so'mga to'ldirildi. Rahmat!")
    except (TelegramForbiddenError, TelegramBadRequest):
        pass


# ============================================================ 4. PREMIUM BERISH
async def give_premium_start(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    await callback.answer()
    await state.set_state(st.GivePremium.waiting_user_id)
    await callback.message.answer("⭐ Foydalanuvchi ID raqamini kiriting:", reply_markup=kb.cancel_kb())


async def give_premium_user_id(message: Message, state: FSMContext, owner_id: int) -> None:
    try:
        user_id = int((message.text or "").strip())
    except ValueError:
        await message.answer("❗️ Faqat raqam kiriting:")
        return
    if db.get_user(owner_id, user_id) is None:
        await message.answer("❗️ Bunday foydalanuvchi botda ro'yxatdan o'tmagan.")
        return
    await state.update_data(user_id=user_id)
    await state.set_state(st.GivePremium.waiting_days)
    await message.answer("📅 Necha kunlik premium beramiz? (raqam kiriting)")


async def give_premium_days(message: Message, state: FSMContext, owner_id: int, bot: Bot) -> None:
    try:
        days = int((message.text or "").strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗️ Musbat raqam kiriting:")
        return
    data = await state.get_data()
    user_id = data["user_id"]
    until = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    db.set_tariff(owner_id, user_id, "PREMIUM", until)
    await state.clear()
    await message.answer(f"✅ {user_id} ga {days} kunlik PREMIUM berildi.", reply_markup=kb.back_to_admin_kb())
    try:
        await bot.send_message(user_id, f"⭐ Sizga {days} kunlik PREMIUM tarif faollashtirildi!")
    except (TelegramForbiddenError, TelegramBadRequest):
        pass


# ============================================================ 5. NARXLARNI BELGILASH
async def prices_menu(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    await state.clear()
    await callback.answer()
    price = db.get_setting(owner_id, "PREMIUM_PRICE")
    days = db.get_setting(owner_id, "PREMIUM_DAYS")
    text = (
        f"💵 <b>Hozirgi narxlar</b>\n\nPREMIUM narxi: {price} so'm\n"
        f"PREMIUM muddati: {days} kun\n\nO'zgartirmoqchi bo'lgan narxni tanlang:"
    )
    await callback.message.answer(text, reply_markup=kb.prices_menu_kb())


async def prices_choose_field(callback: CallbackQuery, state: FSMContext) -> None:
    field = callback.data.split(":")[1]
    await state.update_data(field=field)
    await state.set_state(st.SetPrices.new_value)
    await callback.answer()
    hints = {
        "PREMIUM_PRICE": "Yangi PREMIUM narxini kiriting (faqat raqam):",
        "PREMIUM_DAYS": "Yangi PREMIUM muddatini kiriting (kunlarda, faqat raqam):",
    }
    await callback.message.answer(hints[field])


async def prices_new_value(message: Message, state: FSMContext, owner_id: int) -> None:
    data = await state.get_data()
    field = data["field"]
    value = (message.text or "").strip()

    try:
        int(value)
    except ValueError:
        await message.answer("❗️ Faqat raqam kiriting:")
        return

    db.set_setting(owner_id, field, value)
    await state.clear()
    await message.answer("✅ Narx yangilandi.", reply_markup=kb.back_to_admin_kb())


# ============================================================ 6-7. MAJBURIY OBUNA KANALLAR
async def add_channel_start(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    await callback.answer()
    await state.set_state(st.AddChannel.name)
    await callback.message.answer(
        "📢 Kanal uchun tugma nomini kiriting (masalan: Yangiliklar kanali):",
        reply_markup=kb.cancel_kb(),
    )


async def add_channel_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(st.AddChannel.link)
    await message.answer("🔗 Kanal linkini kiriting (https://t.me/... yoki @username):")


async def add_channel_link(message: Message, state: FSMContext, owner_id: int) -> None:
    link = (message.text or "").strip()
    chat_ref = channel_ref_from_link(link)
    data = await state.get_data()
    display_link = link if link.startswith("http") else f"https://t.me/{chat_ref.lstrip('@')}"
    db.add_channel(owner_id, data["name"], display_link, chat_ref)
    await state.clear()
    await message.answer(
        "✅ Kanal qo'shildi. Diqqat: bot ushbu kanalda ADMIN bo'lishi shart, "
        "aks holda obunani tekshira olmaydi.",
        reply_markup=kb.back_to_admin_kb(),
    )


async def edit_channel_start(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    await callback.answer()
    channels = db.list_channels(owner_id)
    if not channels:
        await callback.message.answer("Hozircha majburiy obuna kanallari yo'q.", reply_markup=kb.back_to_admin_kb())
        return
    await callback.message.answer("📝 Tahrirlamoqchi bo'lgan kanalni tanlang:", reply_markup=kb.channels_list_kb(channels, "selch"))


async def edit_channel_choose(callback: CallbackQuery, state: FSMContext) -> None:
    channel_id = int(callback.data.split(":")[1])
    await callback.answer()
    channel = db.get_channel(channel_id)
    if channel is None:
        await callback.message.answer("Kanal topilmadi.", reply_markup=kb.back_to_admin_kb())
        return
    await callback.message.answer(
        f"📢 {channel['name']}\n🔗 {channel['link']}\n\nAmalni tanlang:",
        reply_markup=kb.channel_edit_fields_kb(channel_id),
    )


async def edit_channel_action(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    _, field, channel_id = callback.data.split(":")
    channel_id = int(channel_id)
    await callback.answer()
    if field == "delete":
        db.delete_channel(channel_id)
        await callback.message.answer("🗑 Kanal o'chirildi.", reply_markup=kb.back_to_admin_kb())
        return
    await state.update_data(field=field, channel_id=channel_id)
    await state.set_state(st.EditChannel.new_value)
    prompt = "Yangi nomni kiriting:" if field == "name" else "Yangi linkni kiriting:"
    await callback.message.answer(prompt)


async def edit_channel_new_value(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    field = data["field"]
    channel_id = data["channel_id"]
    value = (message.text or "").strip()
    if field == "link":
        chat_ref = channel_ref_from_link(value)
        db.update_channel_field(channel_id, "chat_ref", chat_ref)
        db.update_channel_field(channel_id, "link", value if value.startswith("http") else f"https://t.me/{chat_ref.lstrip('@')}")
    else:
        db.update_channel_field(channel_id, field, value)
    await state.clear()
    await message.answer("✅ Kanal yangilandi.", reply_markup=kb.back_to_admin_kb())


# ============================================================ 8. STATISTIKA
async def stats_menu(callback: CallbackQuery, state: FSMContext, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    await state.clear()
    await callback.answer()
    await callback.message.answer("📊 Statistika bo'limini tanlang:", reply_markup=kb.stats_menu_kb())


async def stats_show(callback: CallbackQuery, owner_id: int) -> None:
    if not await _require_admin(callback, owner_id):
        return
    kind = callback.data.split(":")[1]
    await callback.answer()

    if kind == "all":
        users = db.list_users(owner_id)
        header = f"👥 <b>Barcha foydalanuvchilar</b> — jami: {len(users)}\n\n"
        rows = users[:30]
        text = header + "\n".join(format_user_row(u) for u in rows)
        if len(users) > 30:
            text += f"\n... va yana {len(users) - 30} ta"
    elif kind == "premium":
        users = db.list_users(owner_id, "PREMIUM")
        header = f"⭐ <b>Premium foydalanuvchilar</b> — jami: {len(users)}\n\n"
        rows = users[:30]
        text = header + "\n".join(format_user_row(u) for u in rows)
    elif kind == "standard":
        users = db.list_users(owner_id, "STANDARD")
        header = f"🆓 <b>
