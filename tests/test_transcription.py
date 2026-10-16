"""Тесты распознавания речи (фаза 7, D14).

Whisper подменяется поддельным модулем: ни torch, ни модель не загружаются.
"""

import asyncio
import threading
import time
import types
from pathlib import Path

import pytest
from fakes import FakeBot, FakeMessage, FakeVoice

from tune_loader_bot.telegram.voice import TelegramVoiceTranscriber
from tune_loader_bot.transcription.whisper_service import WhisperTranscriber


class ConcurrencyTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def enter(self) -> None:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def exit(self) -> None:
        with self._lock:
            self.active -= 1


class FakeModel:
    def __init__(self, calls: dict, text: str, delay: float, tracker) -> None:
        self._calls = calls
        self._text = text
        self._delay = delay
        self._tracker = tracker

    def transcribe(self, path, language=None):
        if self._tracker:
            self._tracker.enter()
        try:
            self._calls["transcribe"].append(
                {
                    "path": str(path),
                    "language": language,
                    "in_main_thread": threading.current_thread() is threading.main_thread(),
                }
            )
            if self._delay:
                time.sleep(self._delay)
            return {"text": self._text}
        finally:
            if self._tracker:
                self._tracker.exit()


def make_whisper(*, text: str = "привет мир", delay: float = 0.0, tracker=None):
    """Поддельный модуль whisper: модель создаётся один раз."""
    calls: dict = {"load": [], "transcribe": []}
    holder: dict = {}

    def load_model(name):
        calls["load"].append(name)
        if "model" not in holder:
            holder["model"] = FakeModel(calls, text, delay, tracker)
        return holder["model"]

    module = types.ModuleType("whisper")
    module.load_model = load_model
    module.calls = calls
    return module, calls


@pytest.fixture
def audio_file(tmp_path) -> Path:
    path = tmp_path / "voice.ogg"
    path.write_bytes(b"ogg-bytes")
    return path


async def test_lazy_model_load_and_transcribe(settings, audio_file):
    """D14: модель грузится при первом обращении, а не на импорте."""
    module, calls = make_whisper()
    transcriber = WhisperTranscriber(settings, importer=lambda: module)

    assert transcriber.enabled is True
    assert transcriber.model_loaded is False

    assert await transcriber.transcribe(audio_file) == "привет мир"

    assert calls["load"] == [settings.whisper_model]
    assert calls["transcribe"][0]["language"] == settings.whisper_language
    assert transcriber.model_loaded is True


async def test_model_is_loaded_once(settings, audio_file):
    module, calls = make_whisper()
    transcriber = WhisperTranscriber(settings, importer=lambda: module)

    await transcriber.transcribe(audio_file)
    await transcriber.transcribe(audio_file)

    assert calls["load"] == [settings.whisper_model], "модель должна грузиться один раз"
    assert len(calls["transcribe"]) == 2


async def test_transcribe_does_not_block_event_loop(settings, audio_file):
    module, calls = make_whisper()
    transcriber = WhisperTranscriber(settings, importer=lambda: module)

    await transcriber.transcribe(audio_file)

    assert calls["transcribe"][0]["in_main_thread"] is False, "работа должна идти в потоке"


async def test_disabled_by_settings(settings, audio_file):
    module, calls = make_whisper()
    disabled = settings.model_copy(update={"whisper_enabled": False})
    transcriber = WhisperTranscriber(disabled, importer=lambda: module)

    assert transcriber.enabled is False
    assert await transcriber.transcribe(audio_file) is None
    assert calls == {"load": [], "transcribe": []}


async def test_missing_whisper_disables_feature(settings, audio_file, caplog):
    attempts: list[int] = []

    def importer():
        attempts.append(1)
        raise ImportError("No module named 'whisper'")

    transcriber = WhisperTranscriber(settings, importer=importer)

    with caplog.at_level("ERROR"):
        assert await transcriber.transcribe(audio_file) is None
        assert await transcriber.transcribe(audio_file) is None

    assert transcriber.enabled is False
    assert len(attempts) == 1, "повторные попытки импорта не нужны"
    assert "requirements-whisper.txt" in caplog.text


async def test_empty_text_returns_none(settings, audio_file):
    module, _ = make_whisper(text="   ")
    transcriber = WhisperTranscriber(settings, importer=lambda: module)

    assert await transcriber.transcribe(audio_file) is None


async def test_model_errors_are_propagated(settings, audio_file):
    """Ошибку распознавания показывает обработчик («Ошибка распознавания…»)."""

    class BrokenModel:
        def transcribe(self, path, language=None):
            raise RuntimeError("ffmpeg упал")

    module = types.ModuleType("whisper")
    module.load_model = lambda name: BrokenModel()
    transcriber = WhisperTranscriber(settings, importer=lambda: module)

    with pytest.raises(RuntimeError, match="ffmpeg"):
        await transcriber.transcribe(audio_file)


async def test_parallel_transcriptions_are_serialized(settings, audio_file):
    tracker = ConcurrencyTracker()
    module, calls = make_whisper(delay=0.05, tracker=tracker)
    transcriber = WhisperTranscriber(settings, importer=lambda: module)

    await asyncio.gather(
        transcriber.transcribe(audio_file), transcriber.transcribe(audio_file)
    )

    assert len(calls["transcribe"]) == 2
    assert tracker.max_active == 1, "модель не должна распознавать два файла одновременно"


async def test_telegram_transcriber_downloads_and_removes_temp_file(settings):
    module, calls = make_whisper(text="расшифровка")
    service = WhisperTranscriber(settings, importer=lambda: module)
    transcriber = TelegramVoiceTranscriber(service)
    bot = FakeBot()
    message = FakeMessage(voice=FakeVoice(), bot=bot)

    assert transcriber.enabled is True
    assert await transcriber(message) == "расшифровка"

    assert bot.get_file_calls == ["voice-file-id"]
    assert len(bot.downloaded) == 1
    temporary_path = Path(calls["transcribe"][0]["path"])
    assert not temporary_path.exists(), "временный файл должен быть удалён"
    assert transcriber.model_loaded is True


async def test_telegram_transcriber_ignores_message_without_voice(settings):
    module, _ = make_whisper()
    transcriber = TelegramVoiceTranscriber(
        WhisperTranscriber(settings, importer=lambda: module)
    )
    bot = FakeBot()

    assert await transcriber(FakeMessage(text="без голоса", bot=bot)) is None
    assert bot.get_file_calls == []
