import asyncio
import io
import logging
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from pawspot_bot.backend_client import BackendClient, BackendError

logger = logging.getLogger(__name__)
ADD = "📸 Добавить встречу"
ABOUT = "ℹ️ О PawSpot"
TODAY = "🐾 Сегодня встретили"
SKIP = "Пропустить"


def buttons(*rows: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=value)]
            for label, value in rows
        ]
    )


def marker(draft: dict[str, Any]) -> str:
    return str(draft["public_id"]).replace("-", "")[:8]


def choice(action: str, draft: dict[str, Any], value: str = "") -> str:
    draft_key = (
        str(draft["public_id"]).replace("-", "") if action == "save" else marker(draft)
    )
    return f"{action}:{draft_key}:{draft['version']}:{value}"


class BotFlow:
    def __init__(
        self,
        bot: Bot,
        backend: BackendClient,
        max_photo_bytes: int,
        mini_app_url: str = "",
    ) -> None:
        self.bot = bot
        self.backend = backend
        self.max_photo_bytes = max_photo_bytes
        self.mini_app_url = mini_app_url
        # Только состояние текстового поля UI. Доменный draft хранится в backend.
        self.pending: dict[int, str] = {}
        self.delivered: dict[int, str] = {}
        self.last_prompt: dict[int, tuple[str, int, str]] = {}
        self.add_locks: dict[int, asyncio.Lock] = {}

    async def say(self, user_id: int, text: str, **kwargs: Any) -> None:
        await self.bot.send_message(user_id, text, **kwargs)

    def menu(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=ADD)],
                [KeyboardButton(text=TODAY), KeyboardButton(text=ABOUT)],
            ],
            resize_keyboard=True,
        )

    def mini_app_button(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🐾 Открыть PawSpot",
                        web_app=WebAppInfo(url=self.mini_app_url),
                    )
                ]
            ]
        )

    async def error(self, user_id: int, exc: BackendError) -> None:
        if exc.status_code == 403:
            text = "Доступ к закрытому пилоту пока не открыт для вашего аккаунта."
        elif exc.status_code in {404, 410}:
            self.pending.pop(user_id, None)
            text = (
                "Черновик больше недоступен. Нажмите «Добавить встречу», "
                "чтобы начать заново."
            )
        elif exc.status_code == 409:
            text = "Черновик изменился. Показываю его текущее состояние."
        elif exc.status_code in {413, 415}:
            text = (
                "Фото не подошло. Отправьте обычное фото JPEG, PNG или WebP до 10 МБ."
            )
        elif exc.status_code == 422:
            text = "Эти данные не подошли. Проверьте выбор или отправьте другое место."
        else:
            text = "Сервис временно недоступен. Попробуйте ещё раз чуть позже."
        logger.warning("Backend request failed with status %s", exc.status_code)
        await self.say(user_id, text)

    async def start(self, user_id: int, name: str) -> None:
        self.pending.pop(user_id, None)
        self.last_prompt.pop(user_id, None)
        draft = await self.backend.current(user_id, name)
        if draft is None:
            await self.say(
                user_id,
                "Привет! Это PawSpot — здесь можно сохранить встречу с "
                "городским котом или собакой. Нажмите «Добавить встречу» "
                "и отправьте фото. История, карта и коллекция — в Mini App.",
                reply_markup=self.menu(),
            )
        else:
            await self.say(
                user_id, "Продолжим незавершённую встречу.", reply_markup=self.menu()
            )
            await self.render(user_id, name, draft)
        if self.mini_app_url:
            await self.say(
                user_id,
                "Профили животных и карта — в PawSpot.",
                reply_markup=self.mini_app_button(),
            )

    async def about(self, user_id: int) -> None:
        text = (
            "PawSpot сохраняет встречи с городскими котами и собаками. "
            "Сфотографируйте животное и добавьте встречу — с местом или без него.\n\n"
            "Одного персонажа можно встречать много раз: его фотографии и история "
            "сохраняются вместе. В Mini App доступны карта и ваша коллекция.\n\n"
            "На карте видно только приблизительное место. "
            "Точные координаты встречи не публикуются."
        )
        await self.say(
            user_id,
            text,
            reply_markup=self.mini_app_button() if self.mini_app_url else self.menu(),
        )

    async def add(self, user_id: int, name: str) -> None:
        async with self.add_locks.setdefault(user_id, asyncio.Lock()):
            draft = await self.backend.create(user_id, name)
            if draft["state"] == "ready" and user_id in self.pending:
                return
            await self.render(user_id, name, draft)

    def prompt_key(self, draft: dict[str, Any], step: str) -> tuple[str, int, str]:
        return str(draft["public_id"]), int(draft["version"]), step

    def observed_label(self, draft: dict[str, Any]) -> str:
        zone = ZoneInfo(draft["city_timezone"])
        moment = datetime.fromisoformat(draft["observed_at"]).astimezone(zone)
        today = datetime.now(zone).date()
        day = moment.date()
        prefix = (
            "Сегодня"
            if day == today
            else "Вчера"
            if day == today - timedelta(days=1)
            else moment.strftime("%d.%m.%Y")
        )
        return f"{prefix}, {moment:%H:%M}"

    async def ask_location(self, user_id: int, draft: dict[str, Any]) -> None:
        choices = [
            ("📍 Я ещё здесь", choice("here", draft)),
            ("🗺 Указать другое место", choice("elsewhere", draft)),
            ("⏭ Без места", choice("noloc", draft)),
        ]
        if draft["state"] == "ready":
            choices.append(("Оставить место", choice("keepplace", draft)))
        await self.say(
            user_id,
            "📍 Где вы встретили животное? Выберите текущую точку, "
            "отправьте другое место на карте или продолжите без места.",
            reply_markup=buttons(*choices),
        )

    async def today(self, user_id: int, name: str) -> None:
        self.pending.pop(user_id, None)
        self.last_prompt.pop(user_id, None)
        page = await self.backend.today(user_id, name)
        await self.say(
            user_id,
            "🐾 Сегодня в PawSpot\n"
            f"Встреч: {page['encounters']}\n"
            f"🐕 Собаки: {page['dogs']}\n"
            f"🐈 Кошки: {page['cats']}\n"
            f"✨ Впервые добавлено животных: {page['first_animals']}",
        )
        if page["items"]:
            await self.today_card(user_id, name, page, edit_message_id=None)
        else:
            await self.say(user_id, "Сегодня встреч пока нет.")

    async def today_card(
        self, user_id: int, name: str, page: dict[str, Any], edit_message_id: int | None
    ) -> None:
        item = page["items"][0]
        title = item["animal_name"] or "Без имени"
        species = "🐕 Собака" if item["species"] == "dog" else "🐈 Кот"
        zone = ZoneInfo(page["timezone"])
        observed = datetime.fromisoformat(item["observed_at"]).astimezone(zone)
        caption = f"{species} · {title}\n🕒 {observed:%H:%M}"
        if item["repeat_encounter"]:
            caption += "\n🔁 Повторная встреча"
        if item["location_present"]:
            caption += f"\n📍 {item['city_name']} · приблизительное место"
        if item["comment"]:
            caption += f"\n💬 {item['comment'][:200]}"
        total = page["encounters"]
        index = page["page"]
        navigation = []
        if index > 1:
            navigation.append(
                InlineKeyboardButton(text="◀️ Назад", callback_data=f"today:{index - 1}")
            )
        navigation.append(
            InlineKeyboardButton(text=f"{index} / {total}", callback_data="today:noop")
        )
        if index < total:
            navigation.append(
                InlineKeyboardButton(text="Далее ▶️", callback_data=f"today:{index + 1}")
            )
        markup_rows = [navigation]
        if self.mini_app_url:
            base = urlsplit(self.mini_app_url)
            encounter_url = urlunsplit(
                (
                    base.scheme,
                    base.netloc,
                    f"/encounter/{item['encounter_public_id']}",
                    "",
                    "",
                )
            )
            markup_rows.append(
                [
                    InlineKeyboardButton(
                        text="🐾 Открыть в PawSpot",
                        web_app=WebAppInfo(url=encounter_url),
                    )
                ]
            )
        markup = InlineKeyboardMarkup(inline_keyboard=markup_rows)
        photo = await self.backend.photo(
            str(item["photo_public_id"]), user_id, name, variant="main"
        )
        if edit_message_id is None:
            await self.bot.send_photo(
                user_id,
                BufferedInputFile(photo, filename="today.jpg"),
                caption=caption,
                reply_markup=markup,
            )
        else:
            await self.bot.edit_message_media(
                chat_id=user_id,
                message_id=edit_message_id,
                media=InputMediaPhoto(
                    media=BufferedInputFile(photo, filename="today.jpg"),
                    caption=caption,
                ),
                reply_markup=markup,
            )

    async def render(self, user_id: int, name: str, draft: dict[str, Any]) -> None:
        state = draft["state"]
        if self.last_prompt.get(user_id) == self.prompt_key(draft, state):
            return
        cancel = ("Отменить", choice("cancel", draft))
        if state == "need_photo":
            await self.say(user_id, "Отправьте фото животного. /cancel — отмена.")
        elif state == "need_species":
            await self.say(
                user_id,
                "Кого встретили?",
                reply_markup=buttons(
                    ("🐕 Собака", choice("species", draft, "dog")),
                    ("🐈 Кот", choice("species", draft, "cat")),
                    cancel,
                ),
            )
        elif state == "need_location_or_skip":
            await self.ask_location(user_id, draft)
        elif state == "choose_animal":
            candidates = (
                await self.backend.matches(draft, user_id, name)
                if draft["location_present"]
                else await self.backend.collection(user_id, name)
            )
            heading = (
                "Возможно, его уже встречали. Сравните фотографии:"
                if draft["location_present"]
                else "Сравните фото с животными из вашей коллекции:"
            )
            if not candidates:
                heading = "Совпадений не нашлось. Можно создать новое животное."
                await self.say(
                    user_id,
                    heading,
                    reply_markup=buttons(
                        ("🆕 Создать новое животное", choice("animal", draft, "new")),
                        cancel,
                    ),
                )
            else:
                await self.say(user_id, heading)
                for item in candidates[:5]:
                    await self.candidate(user_id, name, draft, item)
        elif state == "ready":
            await self.confirm(user_id, name, draft)
            return
        self.last_prompt[user_id] = self.prompt_key(draft, state)

    async def candidate(
        self, user_id: int, name: str, draft: dict[str, Any], item: dict[str, Any]
    ) -> None:
        species = "собака" if item["species"] == "dog" else "кот"
        title = item["name"] or "Без имени"
        caption = f"{'🐕' if item['species'] == 'dog' else '🐈'} {title} · {species}"
        if item.get("last_observed_at"):
            last_seen = datetime.fromisoformat(item["last_observed_at"])
            caption += f"\nПоследняя встреча: {last_seen:%d.%m.%Y}"
        if item.get("encounter_count"):
            caption += f" · всего встреч: {item['encounter_count']}"
        if not item.get("last_observed_at") and not item.get("encounter_count"):
            caption += "\nИз вашей коллекции"
        animal_id = str(item.get("animal_public_id", item.get("public_id"))).replace(
            "-", ""
        )
        markup = buttons(
            ("✅ Да, это он", choice("animal", draft, animal_id)),
            ("🆕 Нет, это новое животное", choice("animal", draft, "new")),
        )
        photo_id = item.get("thumbnail_photo_id")
        if photo_id:
            try:
                photo = await self.backend.photo(
                    str(photo_id), user_id, name, variant="main"
                )
                await self.bot.send_photo(
                    user_id,
                    BufferedInputFile(photo, filename="candidate.jpg"),
                    caption=caption,
                    reply_markup=markup,
                )
                return
            except (BackendError, TelegramAPIError) as exc:
                logger.warning("Candidate photo unavailable: %s", type(exc).__name__)
        await self.say(
            user_id,
            f"{caption}\nФото недоступно — нельзя проверить совпадение.",
            reply_markup=buttons(
                ("🆕 Нет, это новое животное", choice("animal", draft, "new"))
            ),
        )

    async def confirm(self, user_id: int, name: str, draft: dict[str, Any]) -> None:
        self.pending.pop(user_id, None)
        key = self.prompt_key(draft, "ready")
        if self.last_prompt.get(user_id) == key:
            return
        kind = "🐕 Собака" if draft["species"] == "dog" else "🐈 Кот"
        animal = draft["new_name"] or "без имени"
        if draft["selection"] == "existing":
            animal = draft.get("selected_animal_name") or "без имени"
        selection = "новое" if draft["selection"] == "new" else "уже встречали"
        location = "Место указано" if draft["location_present"] else "Без места"
        observed = self.observed_label(draft)
        caption = (
            "Проверьте встречу перед сохранением:\n"
            f"{kind} · {animal} ({selection})\n"
            f"🕒 {observed}\n"
            f"📍 {location}\n"
            f"💬 {draft['comment'] or 'Без заметки'}"
        )
        rows = [("✅ Сохранить", choice("save", draft))]
        if draft["selection"] == "new":
            rows.append(("Имя", choice("name", draft)))
        rows.extend(
            [
                (
                    "Изменить заметку" if draft["comment"] else "Добавить заметку",
                    choice("comment", draft),
                ),
                ("Изменить время", choice("time", draft)),
                ("Изменить место", choice("place", draft)),
                ("Отменить", choice("cancel", draft)),
            ]
        )
        markup = buttons(*rows)
        photo_id = draft.get("photo_public_id")
        if photo_id:
            try:
                photo = await self.backend.photo(
                    str(photo_id), user_id, name, variant="main"
                )
                await self.bot.send_photo(
                    user_id,
                    BufferedInputFile(photo, filename="preview.jpg"),
                    caption=caption,
                    reply_markup=markup,
                )
            except (BackendError, TelegramAPIError) as exc:
                logger.warning("Preview photo unavailable: %s", type(exc).__name__)
                await self.say(user_id, caption, reply_markup=markup)
        else:
            await self.say(user_id, caption, reply_markup=markup)
        self.last_prompt[user_id] = key

    async def photo(self, message: Message, user_id: int, name: str) -> None:
        draft = await self.backend.create(user_id, name)
        if draft["state"] != "need_photo":
            await self.render(user_id, name, draft)
            return
        if not message.photo:
            return
        photo = message.photo[-1]
        if photo.file_size and photo.file_size > self.max_photo_bytes:
            await self.say(user_id, "Фото слишком большое. Отправьте фото до 10 МБ.")
            return
        try:
            telegram_file = await self.bot.get_file(photo.file_id)
            if not telegram_file.file_path:
                raise OSError("Telegram did not provide a file path")
            output = io.BytesIO()
            await self.bot.download_file(telegram_file.file_path, destination=output)
        except (TelegramAPIError, OSError) as exc:
            logger.warning("Telegram photo download failed: %s", type(exc).__name__)
            await self.say(user_id, "Не удалось скачать фото. Отправьте его ещё раз.")
            return
        data = output.getvalue()
        if len(data) > self.max_photo_bytes:
            await self.say(user_id, "Фото слишком большое. Отправьте фото до 10 МБ.")
            return
        draft = await self.backend.upload(draft, user_id, name, data)
        await self.render(user_id, name, draft)

    async def location(
        self, user_id: int, name: str, latitude: float | None, longitude: float | None
    ) -> None:
        draft = await self.backend.current(user_id, name)
        if draft is None:
            await self.say(user_id, "Сначала добавьте встречу.")
            return
        editing = draft["state"] == "ready" and self.pending.get(user_id) in {
            "current_location",
            "manual_location",
        }
        if draft["state"] != "need_location_or_skip" and not editing:
            await self.render(user_id, name, draft)
            return
        position = (
            {"latitude": latitude, "longitude": longitude}
            if latitude is not None and longitude is not None
            else None
        )
        draft = await self.backend.patch(draft, user_id, name, location=position)
        self.pending.pop(user_id, None)
        await self.say(
            user_id,
            "Место принято." if position else "Место пропущено.",
            reply_markup=self.menu(),
        )
        await self.render(user_id, name, draft)

    async def text(self, user_id: int, name: str, value: str) -> None:
        field = self.pending.get(user_id)
        draft = await self.backend.current(user_id, name)
        if draft is None:
            await self.start(user_id, name)
            return
        if draft["state"] == "need_location_or_skip" and value == SKIP:
            await self.location(user_id, name, None, None)
            return
        if field == "manual_location":
            await self.say(
                user_id,
                "Отправьте точку через скрепку → Геопозиция → Выбрать место "
                "на карте. Или вернитесь к preview.",
            )
            return
        if draft["state"] == "ready" and field == "time_manual":
            value = value.strip()
            if not re.fullmatch(
                r"[0-9]{2}\.[0-9]{2}\.[0-9]{4} [0-9]{2}:[0-9]{2}", value
            ):
                await self.say(
                    user_id,
                    "❌ Не удалось распознать дату и время. "
                    "Используйте формат ДД.ММ.ГГГГ ЧЧ:ММ. "
                    "Например: 03.10.2026 15:20.",
                )
                return
            try:
                zone = ZoneInfo(draft["city_timezone"])
                moment = datetime.strptime(value, "%d.%m.%Y %H:%M").replace(tzinfo=zone)
            except ValueError:
                await self.say(
                    user_id,
                    "❌ Такой даты или времени не существует. "
                    "Проверьте день, месяц, часы и минуты.",
                )
                return
            if moment > datetime.now(zone) + timedelta(minutes=5):
                await self.say(
                    user_id,
                    "❌ Время встречи не может быть в будущем. "
                    "Укажите время, когда вы уже встретили животное.",
                )
                return
            draft = await self.backend.patch(
                draft, user_id, name, observed_at=moment.isoformat()
            )
            self.pending.pop(user_id, None)
            await self.confirm(user_id, name, draft)
            return
        if draft["state"] == "ready" and field in {"name_initial", "name_edit"}:
            if draft["selection"] != "new":
                await self.confirm(user_id, name, draft)
                return
            if len(value) > 80:
                await self.say(user_id, "Имя слишком длинное. До 80 символов.")
                return
            draft = await self.backend.patch(
                draft, user_id, name, new_animal={"name": value}
            )
            self.pending.pop(user_id, None)
            if field == "name_initial":
                await self.ask_comment(user_id, draft)
            else:
                await self.confirm(user_id, name, draft)
        elif draft["state"] == "ready" and field == "comment":
            if len(value) > 500:
                await self.say(user_id, "Заметка слишком длинная. До 500 символов.")
                return
            draft = await self.backend.patch(draft, user_id, name, comment=value)
            self.pending.pop(user_id, None)
            await self.confirm(user_id, name, draft)
        else:
            await self.render(user_id, name, draft)

    async def ask_name(
        self, user_id: int, draft: dict[str, Any], *, from_preview: bool = False
    ) -> None:
        if draft.get("new_name"):
            self.pending.pop(user_id, None)
            await self.say(
                user_id,
                f"Текущее имя: {draft['new_name']}",
                reply_markup=buttons(
                    ("Оставить имя", choice("keepname", draft)),
                    ("Изменить имя", choice("editname", draft)),
                ),
            )
        else:
            self.pending[user_id] = "name_edit" if from_preview else "name_initial"
            await self.say(
                user_id,
                "Как его назовём? Напишите имя или пропустите.",
                reply_markup=buttons(
                    (
                        "Пропустить",
                        choice("keepname" if from_preview else "skipname", draft),
                    ),
                ),
            )
        self.last_prompt[user_id] = self.prompt_key(draft, "name")

    async def ask_comment(self, user_id: int, draft: dict[str, Any]) -> None:
        if draft.get("comment"):
            self.pending.pop(user_id, None)
            await self.say(
                user_id,
                f"Текущая заметка: {draft['comment']}",
                reply_markup=buttons(
                    ("Оставить заметку", choice("keepcomment", draft)),
                    ("Изменить заметку", choice("editcomment", draft)),
                    ("Удалить заметку", choice("clearcomment", draft)),
                ),
            )
        else:
            self.pending[user_id] = "comment"
            await self.say(
                user_id,
                "Что он сегодня делал? Короткая заметка необязательна.",
                reply_markup=buttons(
                    ("Пропустить", choice("skipcomment", draft)),
                ),
            )
        self.last_prompt[user_id] = self.prompt_key(draft, "comment")

    async def callback(self, callback: CallbackQuery, user_id: int, name: str) -> None:
        data = callback.data or ""
        if data.startswith("today:"):
            if data == "today:noop":
                return
            try:
                page_number = int(data.removeprefix("today:"))
            except ValueError:
                return
            if page_number < 1 or page_number > 10000 or callback.message is None:
                return
            page = await self.backend.today(user_id, name, page_number)
            if page["items"]:
                await self.today_card(user_id, name, page, callback.message.message_id)
            return
        parts = data.split(":", 3)
        if len(parts) != 4 or parts[0] not in {
            "species",
            "animal",
            "skipname",
            "skipcomment",
            "keepname",
            "editname",
            "keepcomment",
            "editcomment",
            "clearcomment",
            "name",
            "comment",
            "save",
            "cancel",
            "here",
            "elsewhere",
            "noloc",
            "place",
            "keepplace",
            "time",
            "time_today",
            "time_yesterday",
            "time_manual",
            "time_keep",
        }:
            return
        action, draft_marker, version_text, value = parts
        if action == "save":
            if len(draft_marker) != 32 or not version_text.isdigit():
                return
            if self.delivered.get(user_id) == draft_marker:
                return
            result = await self.backend.commit_id(
                draft_marker, int(version_text), user_id, name
            )
            self.pending.pop(user_id, None)
            self.last_prompt.pop(user_id, None)
            await self.result(user_id, name, result)
            self.delivered[user_id] = draft_marker
            return
        draft = await self.backend.current(user_id, name)
        if draft is None or marker(draft) != draft_marker:
            await self.say(user_id, "Эта кнопка устарела. Нажмите /start.")
            return
        if action == "cancel":
            await self.backend.cancel(draft, user_id, name)
            self.pending.pop(user_id, None)
            self.last_prompt.pop(user_id, None)
            await self.say(user_id, "Встреча отменена. Можно начать заново.")
            return
        if not version_text.isdigit() or int(version_text) != draft["version"]:
            await self.say(user_id, "Эта кнопка устарела. Показываю актуальный шаг.")
            await self.render(user_id, name, draft)
            return
        if action == "place" and draft["state"] == "ready":
            await self.ask_location(user_id, draft)
            self.last_prompt[user_id] = self.prompt_key(draft, "place")
            return
        if action == "keepplace" and draft["state"] == "ready":
            self.pending.pop(user_id, None)
            await self.confirm(user_id, name, draft)
            return
        if action == "here" and draft["state"] in {"need_location_or_skip", "ready"}:
            self.pending[user_id] = "current_location"
            await self.say(
                user_id,
                "Отправьте текущее местоположение, если вы ещё на месте встречи.",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[
                        [
                            KeyboardButton(
                                text="📍 Отправить текущую точку", request_location=True
                            )
                        ]
                    ],
                    resize_keyboard=True,
                    one_time_keyboard=True,
                ),
            )
            return
        if action == "elsewhere" and draft["state"] in {
            "need_location_or_skip",
            "ready",
        }:
            self.pending[user_id] = "manual_location"
            await self.say(
                user_id,
                "Откройте скрепку Telegram → Геопозиция → Выбрать место на карте "
                "и отправьте точку, где была встреча. Не нажимайте "
                "«Отправить мою геопозицию».",
                reply_markup=self.menu(),
            )
            return
        if action == "noloc" and draft["state"] in {"need_location_or_skip", "ready"}:
            await self.location_without_place(user_id, name, draft)
            return
        if action == "time" and draft["state"] == "ready":
            await self.say(
                user_id,
                f"Сейчас: {self.observed_label(draft)}. Когда вы встретили животное?",
                reply_markup=buttons(
                    ("Сегодня · сейчас", choice("time_today", draft)),
                    ("Вчера · в это время", choice("time_yesterday", draft)),
                    ("Ввести дату и время", choice("time_manual", draft)),
                    ("Оставить время", choice("time_keep", draft)),
                ),
            )
            self.last_prompt[user_id] = self.prompt_key(draft, "time")
            return
        if action in {"time_today", "time_yesterday"} and draft["state"] == "ready":
            zone = ZoneInfo(draft["city_timezone"])
            now = datetime.now(zone)
            moment = now if action == "time_today" else now - timedelta(days=1)
            draft = await self.backend.patch(
                draft, user_id, name, observed_at=moment.isoformat()
            )
            await self.confirm(user_id, name, draft)
            return
        if action == "time_manual" and draft["state"] == "ready":
            self.pending[user_id] = "time_manual"
            await self.say(
                user_id,
                "Введите дату и время встречи: ДД.ММ.ГГГГ ЧЧ:ММ "
                "(время города встречи). Например: 03.10.2026 15:20.",
            )
            return
        if action == "time_keep" and draft["state"] == "ready":
            await self.confirm(user_id, name, draft)
            return
        if (
            action == "species"
            and draft["state"] == "need_species"
            and value in {"cat", "dog"}
        ):
            draft = await self.backend.patch(draft, user_id, name, species=value)
            await self.render(user_id, name, draft)
        elif action == "animal" and draft["state"] == "choose_animal":
            if value == "new":
                draft = await self.backend.patch(draft, user_id, name, new_animal={})
                await self.ask_name(user_id, draft)
            elif len(value) == 32:
                draft = await self.backend.patch(
                    draft, user_id, name, animal_public_id=value
                )
                await self.ask_comment(user_id, draft)
        elif draft["state"] == "ready" and action == "skipname":
            await self.ask_comment(user_id, draft)
        elif draft["state"] == "ready" and action == "skipcomment":
            await self.confirm(user_id, name, draft)
        elif draft["state"] == "ready" and action in {"keepname", "keepcomment"}:
            await self.confirm(user_id, name, draft)
        elif (
            draft["state"] == "ready"
            and action == "editname"
            and draft["selection"] == "new"
        ):
            self.pending[user_id] = "name_edit"
            await self.say(user_id, "Напишите новое имя животного.")
        elif draft["state"] == "ready" and action == "editcomment":
            self.pending[user_id] = "comment"
            await self.say(user_id, "Напишите новую заметку.")
        elif draft["state"] == "ready" and action == "clearcomment":
            draft = await self.backend.patch(draft, user_id, name, comment=None)
            await self.confirm(user_id, name, draft)
        elif (
            draft["state"] == "ready"
            and action == "name"
            and draft["selection"] == "new"
        ):
            await self.ask_name(user_id, draft, from_preview=True)
        elif draft["state"] == "ready" and action == "comment":
            await self.ask_comment(user_id, draft)
        else:
            await self.render(user_id, name, draft)

    async def location_without_place(
        self, user_id: int, name: str, draft: dict[str, Any]
    ) -> None:
        draft = await self.backend.patch(draft, user_id, name, location=None)
        self.pending.pop(user_id, None)
        await self.say(user_id, "Продолжаем без места.", reply_markup=self.menu())
        await self.render(user_id, name, draft)

    async def result(self, user_id: int, name: str, result: dict[str, Any]) -> None:
        species = "🐕" if result["species"] == "dog" else "🐈"
        title = result["animal_name"] or "Без имени"
        caption = f"{species} {title}\nВстреча сохранена в PawSpot."
        if result.get("comment"):
            caption += f"\n{result['comment']}"
        try:
            photo = await self.backend.photo(result["photo_public_id"], user_id, name)
            await self.bot.send_photo(
                user_id,
                BufferedInputFile(photo, filename="pawspot.jpg"),
                caption=caption,
                reply_markup=self.menu(),
            )
        except (BackendError, TelegramAPIError) as exc:
            logger.warning("Result photo unavailable: %s", type(exc).__name__)
            await self.say(user_id, caption, reply_markup=self.menu())


def create_dispatcher(flow: BotFlow) -> Dispatcher:
    router = Router()

    @router.message(CommandStart(), F.chat.type == "private")
    async def start(message: Message) -> None:
        if message.from_user is None:
            return
        try:
            await flow.start(message.from_user.id, message.from_user.full_name)
        except BackendError as exc:
            await flow.error(message.from_user.id, exc)

    @router.message(Command("about", "help"), F.chat.type == "private")
    @router.message(F.text == ABOUT, F.chat.type == "private")
    async def about(message: Message) -> None:
        if message.from_user is not None:
            await flow.about(message.from_user.id)

    @router.message(Command("cancel"), F.chat.type == "private")
    async def cancel(message: Message) -> None:
        if message.from_user is None:
            return
        user_id, name = message.from_user.id, message.from_user.full_name
        try:
            draft = await flow.backend.current(user_id, name)
            if draft:
                await flow.backend.cancel(draft, user_id, name)
            flow.pending.pop(user_id, None)
            flow.last_prompt.pop(user_id, None)
            await flow.say(user_id, "Встреча отменена. Можно начать заново.")
        except BackendError as exc:
            await flow.error(user_id, exc)

    @router.message(F.text == ADD, F.chat.type == "private")
    async def add(message: Message) -> None:
        if message.from_user is None:
            return
        try:
            await flow.add(message.from_user.id, message.from_user.full_name)
        except BackendError as exc:
            await flow.error(message.from_user.id, exc)

    @router.message(F.text == TODAY, F.chat.type == "private")
    async def today(message: Message) -> None:
        if message.from_user is None:
            return
        try:
            await flow.today(message.from_user.id, message.from_user.full_name)
        except BackendError as exc:
            await flow.error(message.from_user.id, exc)

    @router.message(F.photo, F.chat.type == "private")
    async def photo(message: Message) -> None:
        if message.from_user is None:
            return
        try:
            await flow.photo(message, message.from_user.id, message.from_user.full_name)
        except BackendError as exc:
            await flow.error(message.from_user.id, exc)

    @router.message(F.location, F.chat.type == "private")
    async def location(message: Message) -> None:
        if message.from_user is None or message.location is None:
            return
        try:
            await flow.location(
                message.from_user.id,
                message.from_user.full_name,
                message.location.latitude,
                message.location.longitude,
            )
        except BackendError as exc:
            await flow.error(message.from_user.id, exc)

    @router.message(F.text, F.chat.type == "private")
    async def text(message: Message) -> None:
        if message.from_user is None or message.text is None:
            return
        try:
            await flow.text(
                message.from_user.id, message.from_user.full_name, message.text
            )
        except BackendError as exc:
            await flow.error(message.from_user.id, exc)

    @router.callback_query(F.message.chat.type == "private")
    async def callback(query: CallbackQuery) -> None:
        await query.answer()
        try:
            await flow.callback(query, query.from_user.id, query.from_user.full_name)
        except BackendError as exc:
            await flow.error(query.from_user.id, exc)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher
