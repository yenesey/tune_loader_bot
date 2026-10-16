"""Все пользовательские строки бота.

Вынесены из `bot.py` без изменения текста: правки формулировок — отдельная
задача, а не побочный эффект рефакторинга.
"""

DIALOG_QUESTION = "Может качнем эту ☝ ссылку?"
DIALOG_STALE = "Ссылка устарела — пришлите её ещё раз."
PROCESSING = "Processing. Please wait for a while..."
SOMETHING_WENT_WRONG = "Something went wrong..."

BUTTON_AUDIO = "🎶Audio"
BUTTON_VIDEO = "📺Video"
BUTTON_EXIT = "❌"

VOICE_RESULT = "🎤 Распознано:\n\n{text}"
VOICE_EMPTY = "Не удалось распознать речь."
VOICE_ERROR = "Ошибка распознавания: {error}"


def voice_result(text: str) -> str:
    return VOICE_RESULT.format(text=text)


def voice_error(error: object) -> str:
    return VOICE_ERROR.format(error=error)
