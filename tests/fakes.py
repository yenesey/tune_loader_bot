"""Ручные заглушки aiogram и yt-dlp для тестов.

В aiogram 3.28.2 нет `MockedBot` (`aiogram.test_utils` удалён), поэтому
заглушки простые и явные: они записывают вызовы, а не имитируют протокол.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tune_loader_bot.media import paths


@dataclass
class FakeUser:
    id: int


@dataclass
class FakeChat:
    id: int


@dataclass
class FakeVoice:
    file_id: str = "voice-file-id"


class FakeMessage:
    """Минимум `aiogram.types.Message`, нужный обработчикам."""

    def __init__(
        self,
        *,
        text: str | None = None,
        voice: FakeVoice | None = None,
        from_user: FakeUser | None = None,
        chat: FakeChat | None = None,
        message_id: int = 1,
        bot: "FakeBot | None" = None,
    ) -> None:
        self.text = text
        self.voice = voice
        self.from_user = from_user
        self.chat = chat or FakeChat(id=100)
        self.message_id = message_id
        self.bot = bot
        self.sent: list[dict] = []
        self.replies: list[str] = []
        self.edited_media: list[object] = []
        self.edited_text: list[str] = []
        self.deleted = 0

    async def answer(self, text=None, **kwargs):
        self.sent.append({"text": text, **kwargs})
        return FakeMessage(bot=self.bot, chat=self.chat, message_id=1000 + len(self.sent))

    async def reply(self, text=None, **kwargs):
        self.replies.append(text)
        return FakeMessage(bot=self.bot, chat=self.chat)

    async def delete(self):
        self.deleted += 1

    async def edit_media(self, media):
        self.edited_media.append(media)

    async def edit_text(self, text, **kwargs):
        self.edited_text.append(text)


class FakeBot:
    """Минимум `aiogram.Bot`: запоминает отправленное."""

    def __init__(self, *, fail_delete: bool = False) -> None:
        self.messages: list[tuple[int, str]] = []
        self.sent_messages: list[FakeMessage] = []
        self.audio: list[tuple[int, dict]] = []
        self.deleted: list[tuple[int, int]] = []
        self.fail_delete = fail_delete
        self.get_file_calls: list = []
        self.downloaded: list[tuple[str, str]] = []
        self._counter = 0

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text))
        self._counter += 1
        message = FakeMessage(
            text=text, chat=FakeChat(id=chat_id), message_id=self._counter, bot=self
        )
        self.sent_messages.append(message)
        return message

    async def send_audio(self, chat_id, audio, **kwargs):
        self.audio.append((chat_id, kwargs))
        return FakeMessage(chat=FakeChat(id=chat_id), bot=self)

    async def delete_message(self, chat_id, message_id):
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.deleted.append((chat_id, message_id))

    async def get_file(self, file_id=None):
        self.get_file_calls.append(file_id)
        return FakeFile()

    async def download_file(self, file_path, destination):
        self.downloaded.append((str(file_path), str(destination)))
        Path(destination).write_bytes(b"ogg-bytes")


class FakeFile:
    def __init__(self, file_path: str = "voice/file.ogg") -> None:
        self.file_path = file_path


class FakeCallbackQuery:
    """Минимум `aiogram.types.CallbackQuery`."""

    def __init__(self, data: str, *, bot: FakeBot, message: FakeMessage | None, from_user=None):
        self.data = data
        self.bot = bot
        self.message = message
        self.from_user = from_user or FakeUser(id=111)
        self.answered = 0

    async def answer(self, *args, **kwargs):
        self.answered += 1


class FakeState:
    """Минимум `FSMContext`: состояние + данные."""

    def __init__(self) -> None:
        self.state = None
        self.data: dict = {}

    async def set_state(self, state):
        self.state = state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.state = None
        self.data.clear()


def make_downloader(
    *,
    file_name: str = "track.mp3",
    content: bytes = b"media-bytes",
    payload: dict | None = None,
    fail: bool = False,
):
    """Поддельная функция скачивания: пишет файл и отдаёт запись."""
    calls: list[dict] = []

    async def downloader(target, service, url, video):
        calls.append({"target": Path(target), "service": service, "url": url, "video": video})
        if fail:
            return None
        path = Path(target) / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        result = {
            "title": "Title",
            "artist": "Artist",
            "channel": "Channel",
            "file_name": file_name,
            "file_path": str(path),
            "file_size": len(content),
        }
        result.update(payload or {})
        return result

    downloader.calls = calls  # type: ignore[attr-defined]
    return downloader


def make_record(settings, **overrides):
    """Запись о загрузке в том виде, в каком её получают доставка и обработчики."""
    record = {
        "title": "Title",
        "artist": "Artist",
        "channel": None,
        "file_name": "track.mp3",
        "file_size": 10,
        "date": datetime(2026, 10, 16, 12, 0),
        "url": "https://youtu.be/dQw4w9WgXcQ",
        "video": 0,
        "delivered": 0,
    }
    record.update(overrides)
    return paths.enrich_info(
        record,
        download_dir=settings.download_dir,
        server_root_url=settings.server_root_url,
    )
