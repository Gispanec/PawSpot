import io
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from pawspot_bot.backend_client import BackendClient, BackendError

logger = logging.getLogger(__name__)
ADD = "📸 Добавить встречу"
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

    async def say(self, user_id: int, text: str, **kwargs: Any) -> None:
        await self.bot.send_message(user_id, text, **kwargs)

    def menu(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=ADD)]], resize_keyboard=True
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
        draft = await self.backend.current(user_id, name)
        if draft is None:
            await self.say(
                user_id,
                "Привет! Сохраним встречу с котом или собакой?",
                reply_markup=self.menu(),
            )
        else:
            await self.say(user_id, "Продолжим незавершённую встречу.")
            await self.render(user_id, name, draft)
        if self.mini_app_url:
            await self.say(
                user_id,
                "Профили животных и карта — в PawSpot.",
                reply_markup=self.mini_app_button(),
            )

    async def add(self, user_id: int, name: str) -> None:
        self.pending.pop(user_id, None)
        await self.render(user_id, name, await self.backend.create(user_id, name))

    async def render(self, user_id: int, name: str, draft: dict[str, Any]) -> None:
        state = draft["state"]
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
            await self.say(
                user_id,
                "Где вы его встретили? Можно пропустить.",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[
                        [
                            KeyboardButton(
                                text="📍 Отправить геолокацию", request_location=True
                            )
                        ],
                        [KeyboardButton(text=SKIP)],
                    ],
                    resize_keyboard=True,
                    one_time_keyboard=True,
                ),
            )
        elif state == "choose_animal":
            candidates = (
                await self.backend.matches(draft, user_id, name)
                if draft["location_present"]
                else await self.backend.collection(user_id, name)
            )
            rows = [
                (
                    f"{'🐕' if item['species'] == 'dog' else '🐈'} "
                    f"{item['name'] or 'Без имени'}",
                    choice(
                        "animal",
                        draft,
                        str(
                            item.get("animal_public_id", item.get("public_id"))
                        ).replace("-", ""),
                    ),
                )
                for item in candidates[:5]
            ]
            rows.extend([("🆕 Новое животное", choice("animal", draft, "new")), cancel])
            heading = (
                "Возможно, его уже встречали:"
                if draft["location_present"]
                else "Можно выбрать животное из своей коллекции:"
            )
            if not candidates:
                heading = "Совпадений не нашлось. Можно создать новое животное."
            await self.say(
                user_id,
                heading,
                reply_markup=buttons(*rows),
            )
        elif state == "ready":
            await self.confirm(user_id, draft)

    async def confirm(self, user_id: int, draft: dict[str, Any]) -> None:
        self.pending.pop(user_id, None)
        kind = "собаку" if draft["species"] == "dog" else "кота"
        animal = (
            draft["new_name"] or "без имени"
            if draft["selection"] == "new"
            else "знакомое животное"
        )
        location = (
            "с приблизительным местом" if draft["location_present"] else "без места"
        )
        await self.say(
            user_id,
            f"Сохранить встречу: {kind}, {animal}, {location}?",
            reply_markup=buttons(
                ("💾 Сохранить", choice("save", draft)),
                ("Имя", choice("name", draft)),
                ("Заметка", choice("comment", draft)),
                ("Отменить", choice("cancel", draft)),
            ),
        )

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
        if draft["state"] != "need_location_or_skip":
            await self.render(user_id, name, draft)
            return
        position = (
            {"latitude": latitude, "longitude": longitude}
            if latitude is not None and longitude is not None
            else None
        )
        draft = await self.backend.patch(draft, user_id, name, location=position)
        await self.say(user_id, "Место принято.", reply_markup=ReplyKeyboardRemove())
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
        if draft["state"] == "ready" and field == "name":
            if draft["selection"] != "new":
                await self.confirm(user_id, draft)
                return
            if len(value) > 80:
                await self.say(user_id, "Имя слишком длинное. До 80 символов.")
                return
            draft = await self.backend.patch(
                draft, user_id, name, new_animal={"name": value}
            )
            self.pending.pop(user_id, None)
            await self.ask_comment(user_id, draft)
        elif draft["state"] == "ready" and field == "comment":
            if len(value) > 500:
                await self.say(user_id, "Заметка слишком длинная. До 500 символов.")
                return
            draft = await self.backend.patch(draft, user_id, name, comment=value)
            await self.confirm(user_id, draft)
        else:
            await self.render(user_id, name, draft)

    async def ask_name(self, user_id: int, draft: dict[str, Any]) -> None:
        self.pending[user_id] = "name"
        await self.say(
            user_id,
            "Как его назовём? Напишите имя или пропустите.",
            reply_markup=buttons(
                ("Пропустить", choice("skipname", draft)),
                ("💾 Сохранить без деталей", choice("save", draft)),
            ),
        )

    async def ask_comment(self, user_id: int, draft: dict[str, Any]) -> None:
        self.pending[user_id] = "comment"
        await self.say(
            user_id,
            "Что он сегодня делал? Короткая заметка необязательна.",
            reply_markup=buttons(
                ("Пропустить", choice("skipcomment", draft)),
                ("💾 Сохранить без заметки", choice("save", draft)),
            ),
        )

    async def callback(self, callback: CallbackQuery, user_id: int, name: str) -> None:
        data = callback.data or ""
        parts = data.split(":", 3)
        if len(parts) != 4 or parts[0] not in {
            "species",
            "animal",
            "skipname",
            "skipcomment",
            "name",
            "comment",
            "save",
            "cancel",
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
            await self.say(user_id, "Встреча отменена. Можно начать заново.")
            return
        if not version_text.isdigit() or int(version_text) != draft["version"]:
            await self.say(user_id, "Эта кнопка устарела. Показываю актуальный шаг.")
            await self.render(user_id, name, draft)
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
            await self.confirm(user_id, draft)
        elif (
            draft["state"] == "ready"
            and action == "name"
            and draft["selection"] == "new"
        ):
            await self.ask_name(user_id, draft)
        elif draft["state"] == "ready" and action == "comment":
            await self.ask_comment(user_id, draft)
        else:
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
            )
        except (BackendError, TelegramAPIError) as exc:
            logger.warning("Result photo unavailable: %s", type(exc).__name__)
            await self.say(user_id, caption)
        await self.say(
            user_id,
            "Чтобы добавить ещё одну встречу, нажмите кнопку ниже.",
            reply_markup=self.menu(),
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
            await flow.error(message.from_user.id, exc)

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
