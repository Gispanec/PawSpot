import asyncio
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
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
WITHOUT_LOCATION = "⏭ Без места"
CANCEL = "❌ Отменить"


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


@dataclass
class PickerState:
    key: tuple[str, int, str]
    page: int = 1
    query: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    selected: str | None = None


@dataclass
class NearbyState:
    key: tuple[str, int, str]
    items: list[dict[str, Any]]
    token: str = field(default_factory=lambda: uuid4().hex[:8])
    revision: int = 0
    index: int = 0
    message_id: int | None = None
    has_photo: bool = False
    confirmable: bool = False


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
        self.pickers: dict[int, PickerState] = {}
        self.nearby: dict[int, NearbyState] = {}
        self.selection_locks: dict[int, asyncio.Lock] = {}
        # После перезапуска клавиатура клиента неизвестна до успешной отправки меню.
        self.reply_menu_users: set[int] = set()

    async def say(self, user_id: int, text: str, **kwargs: Any) -> None:
        markup = kwargs.get("reply_markup")
        if isinstance(markup, ReplyKeyboardMarkup):
            self.reply_menu_users.discard(user_id)
        await self.bot.send_message(user_id, text, **kwargs)
        if isinstance(markup, ReplyKeyboardMarkup) and markup in (
            self.menu(),
            self.active_menu(),
        ):
            self.reply_menu_users.add(user_id)

    def menu(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=ADD)],
                [KeyboardButton(text=TODAY), KeyboardButton(text=ABOUT)],
            ],
            resize_keyboard=True,
        )

    def active_menu(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=ADD)],
                [KeyboardButton(text=CANCEL)],
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

    def encounter_button(
        self, encounter_public_id: str, text: str = "🐾 Открыть встречу"
    ) -> InlineKeyboardButton | None:
        try:
            base = urlsplit(self.mini_app_url)
            if (
                base.scheme != "https"
                or not base.hostname
                or base.username is not None
                or base.password is not None
                or any(char.isspace() for char in base.netloc)
            ):
                return None
            # urlsplit проверяет порт только при обращении к этому свойству.
            _ = base.port
        except ValueError:
            return None
        encounter_url = urlunsplit(
            (base.scheme, base.netloc, f"/encounter/{encounter_public_id}", "", "")
        )
        return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=encounter_url))

    async def error(self, user_id: int, exc: BackendError, name: str) -> None:
        logger.warning("Backend request failed with status %s", exc.status_code)
        if exc.status_code == 409:
            self.clear_ui(user_id)
            try:
                draft = await self.backend.current(user_id, name)
                if draft is None:
                    await self.error(user_id, BackendError(404), name)
                else:
                    await self.say(
                        user_id,
                        "Черновик изменился. Показываю актуальный шаг.",
                        reply_markup=self.active_menu(),
                    )
                    await self.render(user_id, name, draft)
            except BackendError as refresh_error:
                if refresh_error.status_code in {403, 404, 410}:
                    await self.error(user_id, refresh_error, name)
                else:
                    logger.warning(
                        "Draft refresh failed with status %s", refresh_error.status_code
                    )
                    await self.say(
                        user_id,
                        "Не удалось загрузить актуальный черновик. "
                        "Попробуйте /start ещё раз чуть позже.",
                        reply_markup=self.active_menu(),
                    )
            return
        if exc.status_code == 403:
            text = "Доступ к закрытому пилоту пока не открыт для вашего аккаунта."
        elif exc.status_code in {404, 410}:
            self.clear_ui(user_id)
            text = (
                "Черновик больше недоступен. Нажмите «Добавить встречу», "
                "чтобы начать заново."
            )
        elif exc.status_code in {413, 415}:
            text = (
                "Фото не подошло. Отправьте обычное фото JPEG, PNG или WebP до 10 МБ."
            )
        elif exc.status_code == 422:
            text = "Эти данные не подошли. Проверьте выбор или отправьте другое место."
        else:
            text = "Сервис временно недоступен. Попробуйте ещё раз чуть позже."
        await self.say(
            user_id,
            text,
            **({"reply_markup": self.menu()} if exc.status_code in {404, 410} else {}),
        )

    async def retire_callback(self, callback: CallbackQuery, user_id: int) -> None:
        if callback.message is None:
            return
        try:
            await self.bot.edit_message_reply_markup(
                chat_id=user_id,
                message_id=callback.message.message_id,
                reply_markup=None,
            )
        except TelegramAPIError as exc:
            logger.warning("Stale keyboard removal failed: %s", type(exc).__name__)

    async def start(self, user_id: int, name: str) -> None:
        draft = await self.backend.current(user_id, name)
        if draft is None:
            self.clear_ui(user_id)
            await self.say(
                user_id,
                "Привет! Это PawSpot — здесь можно сохранить встречу с "
                "городским котом или собакой. Нажмите «Добавить встречу» "
                "и отправьте фото. История, карта и коллекция — в Mini App.",
                reply_markup=self.menu(),
            )
        else:
            await self.say(
                user_id,
                "Продолжим незавершённую встречу.",
                reply_markup=self.active_menu(),
            )
            await self.resume(user_id, name, draft)
        if self.mini_app_url and draft is None:
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
            draft = await self.backend.current(user_id, name)
            if draft is not None:
                await self.say(
                    user_id,
                    "У вас уже есть незавершённая встреча. "
                    "Продолжить её или начать заново?",
                    reply_markup=buttons(
                        ("▶️ Продолжить", choice("resume", draft)),
                        ("🔄 Начать заново", choice("restart", draft)),
                        (CANCEL, choice("cancel", draft)),
                    ),
                )
                return
            self.clear_ui(user_id)
            draft = await self.backend.create(user_id, name)
            await self.render(user_id, name, draft)

    def clear_ui(self, user_id: int) -> None:
        self.pending.pop(user_id, None)
        self.last_prompt.pop(user_id, None)
        self.pickers.pop(user_id, None)
        self.nearby.pop(user_id, None)

    async def cancel(
        self, user_id: int, name: str, draft: dict[str, Any] | None = None
    ) -> None:
        if draft is None:
            draft = await self.backend.current(user_id, name)
        if draft is not None:
            await self.backend.cancel(draft, user_id, name)
        self.clear_ui(user_id)
        await self.say(
            user_id, "Добавление встречи отменено.", reply_markup=self.menu()
        )

    async def resume(self, user_id: int, name: str, draft: dict[str, Any]) -> None:
        self.last_prompt.pop(user_id, None)
        field = self.pending.get(user_id)
        if draft["state"] == "ready":
            if field == "name_edit" or (field == "comment" and draft.get("comment")):
                await self.say(
                    user_id,
                    "Напишите новое имя животного."
                    if field == "name_edit"
                    else "Напишите новую заметку.",
                    reply_markup=buttons((CANCEL, choice("cancel", draft))),
                )
                return
            if field in {"name_initial", "name_choice"}:
                await self.ask_name(
                    user_id, draft, from_preview=field != "name_initial"
                )
                return
            if field in {"comment", "comment_choice"}:
                await self.ask_comment(user_id, draft)
                return
            if field == "time_choice":
                await self.ask_time_options(user_id, draft)
                return
            if field == "time_manual":
                await self.ask_manual_time(user_id, draft)
                return
            if field == "place_choice":
                await self.ask_location(user_id, draft)
                return
            if field == "current_location":
                await self.ask_current_location(user_id, draft)
                return
            if field == "manual_location":
                await self.ask_manual_location(user_id, draft)
                return
        if draft["state"] == "need_location_or_skip":
            if field == "current_location":
                await self.ask_current_location(user_id, draft)
                return
            if field == "manual_location":
                await self.ask_manual_location(user_id, draft)
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
            (WITHOUT_LOCATION, choice("noloc", draft)),
        ]
        if draft["state"] == "ready":
            choices.append(("Оставить место", choice("keepplace", draft)))
        choices.append((CANCEL, choice("cancel", draft)))
        await self.say(
            user_id,
            "📍 Где вы встретили животное? Выберите текущую точку, "
            "отправьте другое место на карте или продолжите без места.",
            reply_markup=buttons(*choices),
        )

    async def ask_current_location(self, user_id: int, draft: dict[str, Any]) -> None:
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
                    ],
                    [KeyboardButton(text=WITHOUT_LOCATION)],
                    [KeyboardButton(text=CANCEL)],
                ],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )

    async def ask_manual_location(self, user_id: int, draft: dict[str, Any]) -> None:
        self.pending[user_id] = "manual_location"
        await self.say(
            user_id,
            "Откройте скрепку Telegram → Геопозиция → Выбрать место на карте "
            "и отправьте точку, где была встреча. Не нажимайте "
            "«Отправить мою геопозицию».",
            reply_markup=self.active_menu(),
        )

    async def ask_manual_time(self, user_id: int, draft: dict[str, Any]) -> None:
        self.pending[user_id] = "time_manual"
        await self.say(
            user_id,
            "Введите дату и время встречи: ДД.ММ.ГГГГ ЧЧ:ММ "
            "(время города встречи). Например: 03.10.2026 15:20.",
            reply_markup=buttons((CANCEL, choice("cancel", draft))),
        )

    async def ask_time_options(self, user_id: int, draft: dict[str, Any]) -> None:
        self.pending[user_id] = "time_choice"
        await self.say(
            user_id,
            f"Сейчас: {self.observed_label(draft)}. Когда вы встретили животное?",
            reply_markup=buttons(
                ("Сегодня · сейчас", choice("time_today", draft)),
                ("Вчера · в это время", choice("time_yesterday", draft)),
                ("Ввести дату и время", choice("time_manual", draft)),
                ("Оставить время", choice("time_keep", draft)),
                (CANCEL, choice("cancel", draft)),
            ),
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
        encounter_button = self.encounter_button(
            str(item["encounter_public_id"]), "🐾 Открыть в PawSpot"
        )
        if encounter_button is not None:
            markup_rows.append([encounter_button])
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
        cancel = (CANCEL, choice("cancel", draft))
        if state == "need_photo":
            await self.say(
                user_id,
                "Отправьте фото животного. /cancel — отмена.",
                reply_markup=self.active_menu(),
            )
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
            self.pending.pop(user_id, None)
            self.pickers.pop(user_id, None)
            self.nearby.pop(user_id, None)
            if not draft["location_present"]:
                await self.say(
                    user_id,
                    "Вы уже встречали это животное раньше?",
                    reply_markup=buttons(
                        ("🆕 Нет, это новое животное", choice("animal", draft, "new")),
                        ("🐾 Да, выбрать из коллекции", choice("cp", draft, "1")),
                        cancel,
                    ),
                )
                self.last_prompt[user_id] = self.prompt_key(draft, state)
                return
            await self.say(
                user_id,
                "📍 Место указано. Что делаем дальше?",
                reply_markup=buttons(
                    ("🔎 Проверить животных рядом", choice("near", draft)),
                    ("🆕 Это новое животное", choice("animal", draft, "new")),
                    ("🐾 Выбрать из моей коллекции", choice("cp", draft, "1")),
                    cancel,
                ),
            )
        elif state == "ready":
            await self.confirm(user_id, name, draft)
            return
        self.last_prompt[user_id] = self.prompt_key(draft, state)

    async def nearby_card(
        self, user_id: int, name: str, draft: dict[str, Any], state: NearbyState
    ) -> None:
        state.revision += 1
        state.confirmable = False
        item = state.items[state.index]
        token = f"{state.token}.{state.revision}"
        species = "🐕" if item["species"] == "dog" else "🐈"
        caption = f"{species} {item['name'] or 'Без имени'}"
        if item.get("last_observed_at"):
            date = datetime.fromisoformat(item["last_observed_at"]).astimezone(
                ZoneInfo(draft["city_timezone"])
            )
            caption += f"\nПоследняя встреча: {date:%d.%m.%Y}"
        caption += f"\nВсего встреч: {item.get('encounter_count', 0)}"
        caption += f"\n{state.index + 1} из {len(state.items)}"
        photo = None
        if item.get("thumbnail_photo_id"):
            try:
                photo = await self.backend.photo(
                    str(item["thumbnail_photo_id"]), user_id, name, variant="main"
                )
            except BackendError as exc:
                logger.warning("Nearby photo unavailable: %s", exc.status_code)
        rows = []
        if photo is not None:
            rows.append(
                ("✅ Да, это он", choice("nyes", draft, f"{token}.{state.index}"))
            )
        else:
            caption += "\nФото недоступно — нельзя проверить совпадение."
        if state.index > 0:
            rows.append(
                ("⬅️ Назад", choice("npage", draft, f"{token}.{state.index - 1}"))
            )
        if state.index + 1 < len(state.items):
            rows.append(
                ("➡️ Следующее", choice("npage", draft, f"{token}.{state.index + 1}"))
            )
        rows.extend(
            [
                ("↩️ Назад к выбору", choice("cb", draft)),
                ("🆕 Это новое животное", choice("animal", draft, "new")),
                (CANCEL, choice("cancel", draft)),
            ]
        )
        markup = buttons(*rows)
        if state.message_id is not None:
            try:
                if photo is not None:
                    await self.bot.edit_message_media(
                        chat_id=user_id,
                        message_id=state.message_id,
                        media=InputMediaPhoto(
                            media=BufferedInputFile(photo, filename="candidate.jpg"),
                            caption=caption,
                        ),
                        reply_markup=markup,
                    )
                    state.has_photo = state.confirmable = True
                    return
                if not state.has_photo:
                    await self.bot.edit_message_text(
                        caption,
                        chat_id=user_id,
                        message_id=state.message_id,
                        reply_markup=markup,
                    )
                    return
            except TelegramAPIError as exc:
                logger.warning("Nearby card edit failed: %s", type(exc).__name__)
            # Не оставляем фото предыдущего животного рядом с данными следующего.
            try:
                await self.bot.edit_message_reply_markup(
                    chat_id=user_id,
                    message_id=state.message_id,
                    reply_markup=None,
                )
            except TelegramAPIError as exc:
                logger.warning("Nearby keyboard removal failed: %s", type(exc).__name__)
        if photo is not None:
            try:
                sent = await self.bot.send_photo(
                    user_id,
                    BufferedInputFile(photo, filename="candidate.jpg"),
                    caption=caption,
                    reply_markup=markup,
                )
                state.message_id = sent.message_id if sent else None
                state.has_photo = state.confirmable = True
                return
            except TelegramAPIError as exc:
                logger.warning("Nearby photo delivery failed: %s", type(exc).__name__)
                caption += "\nФото недоступно — нельзя проверить совпадение."
                markup = buttons(*rows[1:])
        sent_text = await self.bot.send_message(user_id, caption, reply_markup=markup)
        state.message_id = sent_text.message_id if sent_text else None
        state.has_photo = False

    async def nearby_callback(
        self,
        callback: CallbackQuery,
        user_id: int,
        name: str,
        draft: dict[str, Any],
        action: str,
        value: str,
    ) -> None:
        if draft["state"] != "choose_animal" or not draft["location_present"]:
            return
        key = self.prompt_key(draft, "nearby")
        state = self.nearby.get(user_id)
        if action == "near":
            if self.last_prompt.get(user_id) != self.prompt_key(draft, "choose_animal"):
                return
            items = await self.backend.matches(draft, user_id, name)
            state = NearbyState(key, items[:5])
            self.nearby[user_id] = state
            self.last_prompt[user_id] = key
            await self.retire_callback(callback, user_id)
            if not state.items:
                await self.say(
                    user_id,
                    "Похожих встреч поблизости не найдено.",
                    reply_markup=buttons(
                        ("🆕 Создать новое животное", choice("animal", draft, "new")),
                        ("🐾 Выбрать из моей коллекции", choice("cp", draft, "1")),
                        ("↩️ Назад к выбору", choice("cb", draft)),
                        (CANCEL, choice("cancel", draft)),
                    ),
                )
                return
            await self.nearby_card(user_id, name, draft, state)
            return
        if state is None or state.key != key or self.last_prompt.get(user_id) != key:
            if self.last_prompt.get(user_id) is None:
                await self.resume(user_id, name, draft)
            return
        parts = value.split(".")
        if len(parts) != 3 or parts[:2] != [state.token, str(state.revision)]:
            return
        if not parts[2].isdigit():
            return
        index = int(parts[2])
        if action == "npage":
            if 0 <= index < len(state.items) and abs(index - state.index) == 1:
                state.index = index
                await self.nearby_card(user_id, name, draft, state)
        elif index == state.index and state.confirmable:
            item = state.items[index]
            try:
                draft = await self.backend.patch(
                    draft,
                    user_id,
                    name,
                    animal_public_id=item["animal_public_id"],
                )
            except BackendError as exc:
                if exc.status_code not in {404, 422}:
                    raise exc
                self.clear_ui(user_id)
                await self.retire_callback(callback, user_id)
                await self.say(
                    user_id, "Животное больше недоступно. Проверьте список заново."
                )
                await self.render(user_id, name, draft)
                return
            await self.retire_callback(callback, user_id)
            self.nearby.pop(user_id, None)
            await self.ask_comment(user_id, draft)

    async def candidate(
        self,
        user_id: int,
        name: str,
        draft: dict[str, Any],
        item: dict[str, Any],
        *,
        from_collection: bool = False,
    ) -> None:
        species = "собака" if item["species"] == "dog" else "кот"
        title = item["name"] or "Без имени"
        caption = f"{'🐕' if item['species'] == 'dog' else '🐈'} {title} · {species}"
        if item.get("last_observed_at"):
            last_seen = datetime.fromisoformat(item["last_observed_at"])
            if from_collection:
                last_seen = last_seen.astimezone(ZoneInfo(draft["city_timezone"]))
            caption += f"\nПоследняя встреча: {last_seen:%d.%m.%Y}"
        if item.get("encounter_count"):
            caption += f" · всего встреч: {item['encounter_count']}"
        if not item.get("last_observed_at") and not item.get("encounter_count"):
            caption += "\nИз вашей коллекции"
        animal_id = str(item.get("animal_public_id", item.get("public_id"))).replace(
            "-", ""
        )
        if from_collection:
            caption += f"\nID: #{animal_id[-8:]}"
        rows = [
            (
                "✅ Да, это он",
                choice("ca" if from_collection else "animal", draft, animal_id),
            ),
            ("🆕 Нет, это новое животное", choice("animal", draft, "new")),
            (CANCEL, choice("cancel", draft)),
        ]
        if from_collection:
            rows.insert(1, ("↩️ Вернуться к списку", choice("cl", draft)))
        markup = buttons(*rows)
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
            f"{caption}\n"
            + (
                "Фото недоступно. Подтвердите только если узнали животное по данным."
                if from_collection
                else "Фото недоступно — нельзя проверить совпадение."
            ),
            reply_markup=markup
            if from_collection
            else buttons(
                ("🆕 Нет, это новое животное", choice("animal", draft, "new")),
                (CANCEL, choice("cancel", draft)),
            ),
        )

    def picker_state(self, user_id: int, draft: dict[str, Any]) -> PickerState:
        key = self.prompt_key(draft, str(draft["species"]))
        state = self.pickers.get(user_id)
        if state is None or state.key != key:
            state = PickerState(key)
            self.pickers[user_id] = state
        return state

    async def collection_page(
        self,
        user_id: int,
        name: str,
        draft: dict[str, Any],
        page_number: int,
        *,
        edit_message_id: int | None = None,
    ) -> None:
        state = self.picker_state(user_id, draft)
        page = await self.backend.collection_picker(
            user_id, name, draft["species"], page_number, state.query
        )
        if not page["items"] and page_number > 1:
            # Коллекция могла сократиться; возвращаем действительную страницу.
            page = await self.backend.collection_picker(
                user_id, name, draft["species"], 1, state.query
            )
        state.page = page["page"]
        state.items = page["items"]
        state.selected = None
        self.pending.pop(user_id, None)
        kind = "🐕 Выберите собаку" if draft["species"] == "dog" else "🐈 Выберите кота"
        text = f"{kind} из вашей коллекции\nСтраница {state.page}."
        if state.query:
            text += f"\nПоиск: {state.query}"
        rows = []
        zone = ZoneInfo(draft["city_timezone"])
        for item in state.items:
            animal_id = str(item["animal_public_id"]).replace("-", "")
            date = datetime.fromisoformat(item["last_observed_at"]).astimezone(zone)
            label = f"{item['name'] or 'Без имени'} · {date:%d.%m} · #{animal_id[-8:]}"
            rows.append((label, choice("co", draft, animal_id)))
        if not state.items:
            text += (
                "\nНичего не найдено. Измените запрос или вернитесь ко всей коллекции."
                if state.query
                else "\nВ вашей коллекции пока нет животных этого вида."
            )
        if state.page > 1:
            rows.append(("⬅️ Назад", choice("cp", draft, str(state.page - 1))))
        if page["has_next"]:
            rows.append(("➡️ Далее", choice("cp", draft, str(state.page + 1))))
        rows.append(("🔎 Найти по имени", choice("cs", draft)))
        if state.query:
            rows.append(("Сбросить поиск", choice("cc", draft)))
        rows.extend(
            [
                ("🆕 Это новое животное", choice("animal", draft, "new")),
                ("↩️ К выбору", choice("cb", draft)),
                (CANCEL, choice("cancel", draft)),
            ]
        )
        markup = buttons(*rows)
        if edit_message_id is not None:
            try:
                await self.bot.edit_message_text(
                    text,
                    chat_id=user_id,
                    message_id=edit_message_id,
                    reply_markup=markup,
                )
            except TelegramAPIError as exc:
                if "message is not modified" not in str(exc).lower():
                    await self.say(user_id, text, reply_markup=markup)
        else:
            await self.say(user_id, text, reply_markup=markup)
        self.last_prompt[user_id] = self.prompt_key(draft, "collection")

    async def unavailable_animal(
        self,
        user_id: int,
        name: str,
        draft: dict[str, Any],
        exc: BackendError,
    ) -> None:
        if exc.status_code not in {404, 422}:
            raise exc
        await self.say(
            user_id, "Животное больше недоступно. Выберите из актуального списка."
        )
        await self.collection_page(
            user_id, name, draft, self.picker_state(user_id, draft).page
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
                (CANCEL, choice("cancel", draft)),
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
        if position is None:
            await self.location_without_place(user_id, name, draft)
            return
        draft = await self.backend.patch(draft, user_id, name, location=position)
        self.pending.pop(user_id, None)
        await self.say(
            user_id,
            "Место принято.",
            reply_markup=self.active_menu(),
        )
        await self.render(user_id, name, draft)

    async def text(self, user_id: int, name: str, value: str) -> None:
        field = self.pending.get(user_id)
        draft = await self.backend.current(user_id, name)
        if draft is None:
            await self.start(user_id, name)
            return
        awaiting_location = draft["state"] == "need_location_or_skip" or (
            draft["state"] == "ready"
            and field in {"current_location", "manual_location"}
        )
        if awaiting_location and value in {SKIP, WITHOUT_LOCATION}:
            await self.location_without_place(user_id, name, draft)
            return
        if awaiting_location and field == "current_location":
            await self.say(
                user_id,
                "📍 Сейчас ожидаю геопозицию. Нажмите «Отправить текущую точку» "
                "внизу или выберите «⏭ Без места».",
            )
            return
        if draft["state"] == "choose_animal" and field == "collection_search":
            state = self.pickers.get(user_id)
            if state is None or state.key != self.prompt_key(
                draft, str(draft["species"])
            ):
                self.clear_ui(user_id)
                await self.render(user_id, name, draft)
                return
            query = value.strip()
            if not query or len(query) > 80:
                await self.say(user_id, "Отправьте часть имени: от 1 до 80 символов.")
                return
            state.query = query
            await self.collection_page(user_id, name, draft, 1)
            return
        if awaiting_location and field == "manual_location":
            await self.say(
                user_id,
                "Отправьте точку через скрепку → Геопозиция → Выбрать место "
                "на карте. Или вернитесь к preview.",
            )
            return
        hints = {
            "need_photo": "📸 Отправьте фотографию животного, чтобы продолжить.",
            "need_species": "Выберите кошку или собаку с помощью кнопок выше.",
            "need_location_or_skip": (
                "📍 Выберите место с помощью кнопок выше или продолжите без места."
            ),
            "choose_animal": "Выберите вариант с помощью кнопок выше.",
        }
        if draft["state"] in hints:
            await self.say(user_id, hints[draft["state"]])
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
            self.pending[user_id] = "name_choice"
            await self.say(
                user_id,
                f"Текущее имя: {draft['new_name']}",
                reply_markup=buttons(
                    ("Оставить имя", choice("keepname", draft)),
                    ("Изменить имя", choice("editname", draft)),
                    (CANCEL, choice("cancel", draft)),
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
                    (CANCEL, choice("cancel", draft)),
                ),
            )
        self.last_prompt[user_id] = self.prompt_key(draft, "name")

    async def ask_comment(self, user_id: int, draft: dict[str, Any]) -> None:
        if draft.get("comment"):
            self.pending[user_id] = "comment_choice"
            await self.say(
                user_id,
                f"Текущая заметка: {draft['comment']}",
                reply_markup=buttons(
                    ("Оставить заметку", choice("keepcomment", draft)),
                    ("Изменить заметку", choice("editcomment", draft)),
                    ("Удалить заметку", choice("clearcomment", draft)),
                    (CANCEL, choice("cancel", draft)),
                ),
            )
        else:
            self.pending[user_id] = "comment"
            await self.say(
                user_id,
                "Что он сегодня делал? Короткая заметка необязательна.",
                reply_markup=buttons(
                    ("Пропустить", choice("skipcomment", draft)),
                    (CANCEL, choice("cancel", draft)),
                ),
            )
        self.last_prompt[user_id] = self.prompt_key(draft, "comment")

    async def callback(self, callback: CallbackQuery, user_id: int, name: str) -> None:
        # Перелистывание и подтверждение одной карточки обрабатываются последовательно.
        action = (callback.data or "").split(":", 1)[0]
        if action in {
            "near",
            "npage",
            "nyes",
            "cp",
            "cs",
            "cc",
            "cb",
            "cl",
            "co",
            "ca",
            "animal",
            "cancel",
            "resume",
            "restart_yes",
        }:
            async with self.selection_locks.setdefault(user_id, asyncio.Lock()):
                await self.handle_callback(callback, user_id, name)
        else:
            await self.handle_callback(callback, user_id, name)

    async def handle_callback(
        self, callback: CallbackQuery, user_id: int, name: str
    ) -> None:
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
            "near",
            "npage",
            "nyes",
            "cp",
            "cs",
            "cc",
            "cb",
            "cl",
            "co",
            "ca",
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
            "resume",
            "restart",
            "restart_yes",
            "restart_no",
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
            self.clear_ui(user_id)
            # Commit уже успешен: ошибка уведомления не должна повторять сохранение.
            self.delivered[user_id] = draft_marker
            await self.result(user_id, name, result)
            return
        draft = await self.backend.current(user_id, name)
        if draft is None or marker(draft) != draft_marker:
            await self.retire_callback(callback, user_id)
            await self.say(user_id, "Эта кнопка устарела. Нажмите /start.")
            return
        if not version_text.isdigit() or int(version_text) != draft["version"]:
            await self.retire_callback(callback, user_id)
            self.clear_ui(user_id)
            await self.say(user_id, "Эта кнопка устарела. Показываю актуальный шаг.")
            await self.render(user_id, name, draft)
            return
        if action == "cancel":
            await self.cancel(user_id, name, draft)
            return
        if action in {"near", "npage", "nyes"}:
            await self.nearby_callback(callback, user_id, name, draft, action, value)
            return
        if action in {"cp", "cs", "cc", "cb", "cl", "co", "ca"}:
            if draft["state"] != "choose_animal":
                await self.retire_callback(callback, user_id)
                await self.resume(user_id, name, draft)
                return
            self.nearby.pop(user_id, None)
            state = self.picker_state(user_id, draft)
            if action == "cb":
                await self.retire_callback(callback, user_id)
                self.clear_ui(user_id)
                await self.render(user_id, name, draft)
            elif action == "cs":
                state.selected = None
                self.pending[user_id] = "collection_search"
                await self.say(
                    user_id,
                    "Отправьте часть имени животного (до 80 символов).",
                    reply_markup=buttons(
                        ("↩️ Вернуться к списку", choice("cl", draft)),
                        ("🆕 Это новое животное", choice("animal", draft, "new")),
                        (CANCEL, choice("cancel", draft)),
                    ),
                )
            elif action in {"cp", "cc", "cl"}:
                if action == "cp" and (
                    not value.isdigit() or not 1 <= int(value) <= 100000
                ):
                    return
                if action == "cc":
                    state.query = ""
                await self.collection_page(
                    user_id,
                    name,
                    draft,
                    int(value)
                    if action == "cp"
                    else 1
                    if action == "cc"
                    else state.page,
                    edit_message_id=(
                        callback.message.message_id
                        if callback.message and action in {"cp", "cc"}
                        else None
                    ),
                )
                if action == "cl":
                    await self.retire_callback(callback, user_id)
            elif action == "co":
                if state.selected == value:
                    return
                if not any(
                    str(i["animal_public_id"]).replace("-", "") == value
                    for i in state.items
                ):
                    await self.collection_page(user_id, name, draft, state.page)
                    return
                try:
                    item = await self.backend.collection_animal(
                        user_id, name, draft["species"], value
                    )
                except BackendError as exc:
                    await self.unavailable_animal(user_id, name, draft, exc)
                    return
                await self.retire_callback(callback, user_id)
                self.pending.pop(user_id, None)
                state.selected = value
                await self.candidate(user_id, name, draft, item, from_collection=True)
            elif action == "ca":
                if state.selected != value:
                    await self.retire_callback(callback, user_id)
                    await self.collection_page(user_id, name, draft, state.page)
                    return
                try:
                    draft = await self.backend.patch(
                        draft, user_id, name, animal_public_id=value
                    )
                except BackendError as exc:
                    await self.unavailable_animal(user_id, name, draft, exc)
                    return
                await self.retire_callback(callback, user_id)
                self.pickers.pop(user_id, None)
                await self.ask_comment(user_id, draft)
            return
        if action in {"resume", "restart_no"}:
            await self.resume(user_id, name, draft)
            return
        if action == "restart":
            await self.say(
                user_id,
                "Текущая незавершённая встреча будет удалена. Начать заново?",
                reply_markup=buttons(
                    ("✅ Да, начать заново", choice("restart_yes", draft)),
                    ("↩️ Нет, продолжить", choice("restart_no", draft)),
                    (CANCEL, choice("cancel", draft)),
                ),
            )
            return
        if action == "restart_yes":
            async with self.add_locks.setdefault(user_id, asyncio.Lock()):
                await self.backend.cancel(draft, user_id, name)
                self.clear_ui(user_id)
                replacement = await self.backend.create(user_id, name)
                await self.render(user_id, name, replacement)
            return
        if action == "place" and draft["state"] == "ready":
            self.pending[user_id] = "place_choice"
            await self.ask_location(user_id, draft)
            self.last_prompt[user_id] = self.prompt_key(draft, "place")
            return
        if action == "keepplace" and draft["state"] == "ready":
            self.pending.pop(user_id, None)
            await self.confirm(user_id, name, draft)
            return
        if action == "here" and draft["state"] in {"need_location_or_skip", "ready"}:
            await self.ask_current_location(user_id, draft)
            return
        if action == "elsewhere" and draft["state"] in {
            "need_location_or_skip",
            "ready",
        }:
            await self.ask_manual_location(user_id, draft)
            return
        if action == "noloc" and draft["state"] in {"need_location_or_skip", "ready"}:
            await self.location_without_place(user_id, name, draft)
            return
        if action == "time" and draft["state"] == "ready":
            await self.ask_time_options(user_id, draft)
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
            await self.ask_manual_time(user_id, draft)
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
                self.pickers.pop(user_id, None)
                self.nearby.pop(user_id, None)
                await self.ask_name(user_id, draft)
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
            await self.say(
                user_id,
                "Напишите новое имя животного.",
                reply_markup=buttons((CANCEL, choice("cancel", draft))),
            )
        elif draft["state"] == "ready" and action == "editcomment":
            self.pending[user_id] = "comment"
            await self.say(
                user_id,
                "Напишите новую заметку.",
                reply_markup=buttons((CANCEL, choice("cancel", draft))),
            )
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
        if user_id not in self.reply_menu_users:
            await self.say(
                user_id, "📍 Место не указано", reply_markup=self.active_menu()
            )
        await self.render(user_id, name, draft)

    async def result(self, user_id: int, name: str, result: dict[str, Any]) -> None:
        species = "🐕" if result["species"] == "dog" else "🐈"
        title = result["animal_name"] or "Без имени"
        caption = f"{species} {title}\nВстреча сохранена в PawSpot."
        if result.get("comment"):
            caption += f"\n{result['comment']}"
        button = self.encounter_button(str(result["encounter_public_id"]))
        markup: InlineKeyboardMarkup | ReplyKeyboardMarkup = self.menu()
        if button is not None:
            markup = InlineKeyboardMarkup(inline_keyboard=[[button]])
            try:
                await self.say(
                    user_id,
                    "✅ Готово",
                    reply_markup=self.menu(),
                )
            except TelegramAPIError as exc:
                logger.warning("Result menu delivery failed: %s", type(exc).__name__)
        try:
            photo = await self.backend.photo(result["photo_public_id"], user_id, name)
            await self.bot.send_photo(
                user_id,
                BufferedInputFile(photo, filename="pawspot.jpg"),
                caption=caption,
                reply_markup=markup,
            )
            if isinstance(markup, ReplyKeyboardMarkup):
                self.reply_menu_users.add(user_id)
        except (BackendError, TelegramAPIError) as exc:
            logger.warning("Result photo unavailable: %s", type(exc).__name__)
            try:
                await self.say(user_id, caption, reply_markup=markup)
            except TelegramAPIError as exc:
                logger.warning(
                    "Result confirmation delivery failed: %s", type(exc).__name__
                )


def create_dispatcher(flow: BotFlow) -> Dispatcher:
    router = Router()

    @router.message(CommandStart(), F.chat.type == "private")
    async def start(message: Message) -> None:
        if message.from_user is None:
            return
        try:
            await flow.start(message.from_user.id, message.from_user.full_name)
        except BackendError as exc:
            await flow.error(message.from_user.id, exc, message.from_user.full_name)

    @router.message(Command("about", "help"), F.chat.type == "private")
    @router.message(F.text == ABOUT, F.chat.type == "private")
    async def about(message: Message) -> None:
        if message.from_user is not None:
            await flow.about(message.from_user.id)

    @router.message(Command("cancel"), F.chat.type == "private")
    @router.message(F.text == CANCEL, F.chat.type == "private")
    async def cancel(message: Message) -> None:
        if message.from_user is None:
            return
        user_id, name = message.from_user.id, message.from_user.full_name
        try:
            await flow.cancel(user_id, name)
        except BackendError as exc:
            await flow.error(user_id, exc, name)

    @router.message(F.text == ADD, F.chat.type == "private")
    async def add(message: Message) -> None:
        if message.from_user is None:
            return
        try:
            await flow.add(message.from_user.id, message.from_user.full_name)
        except BackendError as exc:
            await flow.error(message.from_user.id, exc, message.from_user.full_name)

    @router.message(F.text == TODAY, F.chat.type == "private")
    async def today(message: Message) -> None:
        if message.from_user is None:
            return
        try:
            await flow.today(message.from_user.id, message.from_user.full_name)
        except BackendError as exc:
            await flow.error(message.from_user.id, exc, message.from_user.full_name)

    @router.message(F.photo, F.chat.type == "private")
    async def photo(message: Message) -> None:
        if message.from_user is None:
            return
        try:
            await flow.photo(message, message.from_user.id, message.from_user.full_name)
        except BackendError as exc:
            await flow.error(message.from_user.id, exc, message.from_user.full_name)

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
            await flow.error(message.from_user.id, exc, message.from_user.full_name)

    @router.message(F.text, F.chat.type == "private")
    async def text(message: Message) -> None:
        if message.from_user is None or message.text is None:
            return
        try:
            await flow.text(
                message.from_user.id, message.from_user.full_name, message.text
            )
        except BackendError as exc:
            await flow.error(message.from_user.id, exc, message.from_user.full_name)

    @router.callback_query(F.message.chat.type == "private")
    async def callback(query: CallbackQuery) -> None:
        await query.answer()
        try:
            await flow.callback(query, query.from_user.id, query.from_user.full_name)
        except BackendError as exc:
            if exc.status_code in {404, 409, 410}:
                await flow.retire_callback(query, query.from_user.id)
            await flow.error(query.from_user.id, exc, query.from_user.full_name)

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher
