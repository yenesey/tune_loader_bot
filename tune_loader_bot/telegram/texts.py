"""Все пользовательские строки бота.

Собраны в одном месте: правки формулировок не должны трогать логику.
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
