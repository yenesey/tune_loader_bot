"""Тесты сборки параметров yt-dlp (`build_options`).

Здесь закрываются D3 (глобальный мутируемый словарь), D4 (неверная вложенность
`extractor_args`), D22 (пути к cookies/Deno) и D23 (`noplaylist`).
"""

from pathlib import Path

import pytest

from tune_loader_bot.config import Settings
from tune_loader_bot.media import ytdlp


def make_settings(**overrides) -> Settings:
    data = {
        "telegram-api-token": "t",
        "download-dir": "/srv/media",
        "server-root-url": "https://example.test/media",
        "users-list": [111],
        "po-token-gvs": "gvs-token",
        "po-token-web": "web-token",
        "cookies-file": "cookies.txt",
        "deno-path": "/usr/local/bin/deno",
    }
    data.update(overrides)
    return Settings.model_validate(data)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(
        **{"download-dir": str(tmp_path / "media"), "cookies-file": str(tmp_path / "cookies.txt")}
    )


def test_no_module_level_options_dict():
    """D3: раньше параметры жили в глобальном `YTDL_OPTS` и мутировали."""
    assert not hasattr(ytdlp, "YTDL_OPTS")


def test_options_are_independent(settings):
    video_options = ytdlp.build_options(settings, "/tmp/target", service="youtube", video=True)
    audio_options = ytdlp.build_options(settings, "/tmp/target", service="youtube", video=False)

    video_options["format"] = "broken"
    video_options["paths"]["home"] = "/tmp/broken"
    video_options["postprocessors"].append({"key": "Bogus"})
    video_options["extractor_args"]["youtube"]["player_client"].append("android")

    assert audio_options["format"] == "bestaudio/best"
    assert audio_options["paths"]["home"] == "/tmp/target"
    assert audio_options["postprocessors"] == [
        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
    ]
    assert audio_options["extractor_args"]["youtube"]["player_client"] == ["web"]


def test_playlist_options_do_not_inherit_media_options(settings):
    """D3: настройки плейлиста больше не «наследуют» последний format/постпроцессор."""
    ytdlp.build_options(settings, "/tmp/target", service="youtube", video=True)

    playlist_options = ytdlp.build_options(settings, "/tmp/target", for_playlist=True)

    assert "format" not in playlist_options
    assert "noplaylist" not in playlist_options
    assert playlist_options["postprocessors"] == []
    assert playlist_options["skip_download"] is True
    assert playlist_options["extract_flat"] == "in_playlist"
    assert playlist_options["ignoreerrors"] is True


def test_extractor_args_shape_D4(settings):
    """D4: аргументы должны лежать внутри имени экстрактора `youtube`."""
    options = ytdlp.build_options(settings, "/tmp/target", service="youtube")

    assert set(options["extractor_args"]) == {"youtube"}
    youtube = options["extractor_args"]["youtube"]
    assert youtube["player_client"] == ["web"]
    assert youtube["po_token"] == ["web.gvs+gvs-token", "web.player+web-token"]
    assert youtube["player_js_version"] == ["actual"]
    assert youtube["player_js_variant"] == ["main"]
    # Все значения обязаны быть списками: строку yt-dlp разбирает по символам
    # (`"actual"` → `"a"`), что и было поймано проверкой на живом yt-dlp.
    assert all(isinstance(value, list) for value in youtube.values())
    # старые (нерабочие) ключи верхнего уровня
    assert "player_client" not in options["extractor_args"]
    assert "youtube_player_js_version" not in options["extractor_args"]


def test_extractor_args_without_tokens(settings, caplog):
    options = ytdlp.build_options(
        make_settings(**{"po-token-gvs": "", "po-token-web": ""}), "/tmp/target", service="youtube"
    )

    assert "po_token" not in options["extractor_args"]["youtube"]


@pytest.mark.parametrize(
    ("service", "video", "expected"),
    [
        ("youtube", True, "(mp4,webm)[height<960]"),
        ("youtube", False, "bestaudio/best"),
        ("soundcloud", False, "bestvideo*+bestaudio/best"),
        ("tiktok", True, "bestvideo*+bestaudio/best"),
        (None, False, "bestvideo*+bestaudio/best"),
    ],
)
def test_format_selector_per_service(settings, service, video, expected):
    options = ytdlp.build_options(settings, "/tmp/target", service=service, video=video)

    assert options["format"] == expected


def test_postprocessors_audio_and_video(settings):
    audio = ytdlp.build_options(settings, "/tmp/target", video=False)
    video = ytdlp.build_options(settings, "/tmp/target", video=True)

    assert audio["postprocessors"][0]["key"] == "FFmpegExtractAudio"
    assert audio["postprocessors"][0]["preferredcodec"] == "mp3"
    assert video["postprocessors"] == [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]


def test_noplaylist_only_for_single_media_D23(settings):
    media = ytdlp.build_options(settings, "/tmp/target", service="youtube")
    playlist = ytdlp.build_options(settings, "/tmp/target", for_playlist=True)

    assert media["noplaylist"] is True
    assert "noplaylist" not in playlist


def test_playlist_items_default_and_override(settings):
    default = ytdlp.build_options(settings, "/tmp/target", for_playlist=True)
    override = ytdlp.build_options(settings, "/tmp/target", for_playlist=True, playlist_items="1")

    assert default["playlist_items"] == settings.watch_playlist_items
    assert override["playlist_items"] == "1"


def test_cookiefile_is_used_only_when_present(settings, caplog):
    missing = ytdlp.build_options(settings, "/tmp/target", service="youtube")
    assert "cookiefile" not in missing
    assert "cookies" in caplog.text

    Path(settings.cookies_file).write_text("# cookies", encoding="utf-8")
    present = ytdlp.build_options(settings, "/tmp/target", service="youtube")
    assert present["cookiefile"] == settings.cookies_file


def test_deno_path_from_settings(settings):
    options = ytdlp.build_options(settings, "/tmp/target", service="youtube")

    assert options["js_runtimes"] == {"deno": {"path": "/usr/local/bin/deno"}}


def test_deno_path_falls_back_to_path_lookup(monkeypatch, settings, caplog):
    monkeypatch.setattr(ytdlp.shutil, "which", lambda name: "/found/deno")

    from_path = ytdlp.build_options(make_settings(**{"deno-path": None}), "/tmp/target")
    assert from_path["js_runtimes"] == {"deno": {"path": "/found/deno"}}

    monkeypatch.setattr(ytdlp.shutil, "which", lambda name: None)
    without_deno = ytdlp.build_options(make_settings(**{"deno-path": None}), "/tmp/target")
    assert "js_runtimes" not in without_deno
    assert "Deno" in caplog.text


def test_retries_and_timeout_are_set(settings):
    options = ytdlp.build_options(settings, "/tmp/target", retries=5, socket_timeout=7)

    assert options["retries"] == 5
    assert options["fragment_retries"] == 5
    assert options["socket_timeout"] == 7
    assert options["extractor_retries"] == ytdlp.DEFAULT_EXTRACTOR_RETRIES
    assert options["noprogress"] is True and options["quiet"] is True


def test_outtmpl_comes_from_settings(settings):
    templated = ytdlp.build_options(
        make_settings(**{"filename-template": "%(title).100s [%(id)s]"}), "/tmp/target"
    )

    assert templated["outtmpl"] == "%(title).100s [%(id)s]"
    assert "%(id)s" not in ytdlp.build_options(settings, "/tmp/target")["outtmpl"]


def test_postprocessor_hook_is_passed_through(settings):
    hook = ytdlp.MoveFilesHook()

    options = ytdlp.build_options(settings, "/tmp/target", postprocessor_hook=hook)

    assert options["postprocessor_hooks"] == [hook]
