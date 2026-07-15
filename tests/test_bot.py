"""Tests for src/bot.py: post_init, error_handler and build_application."""

from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Update

from src.bot import ERROR_MESSAGE, build_application, error_handler, post_init

# --- post_init --------------------------------------------------------------


async def test_post_init_sets_commands():
    application = MagicMock()
    application.bot = AsyncMock()

    await post_init(application)

    application.bot.set_my_commands.assert_called_once()
    commands = application.bot.set_my_commands.call_args[0][0]
    assert ("start", "Start working with the bot") in commands
    assert ("list", "Show list of subscribed channels") in commands


async def test_post_init_swallows_errors():
    """A failure to register commands is logged, not raised (the bot still runs)."""
    application = MagicMock()
    application.bot = AsyncMock()
    application.bot.set_my_commands.side_effect = Exception("Failed to set commands")

    with patch("src.bot.logging.error") as mock_log:
        await post_init(application)  # must not raise

    mock_log.assert_called_once()
    assert "Failed to set up bot commands" in mock_log.call_args[0][0]


# --- error_handler ----------------------------------------------------------


async def test_error_handler_notifies_user_and_logs():
    """With an effective chat, the handler logs the error and replies with ERROR_MESSAGE."""
    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock()
    update.effective_chat.id = 42

    context = MagicMock()
    context.error = ValueError("boom")
    context.bot = AsyncMock()

    with patch("src.bot.logging.error") as mock_log:
        await error_handler(update, context)

    mock_log.assert_called_once()
    # exc_info carries the original exception
    assert mock_log.call_args.kwargs.get("exc_info") is context.error
    context.bot.send_message.assert_called_once_with(chat_id=42, text=ERROR_MESSAGE)


async def test_error_handler_without_effective_chat_only_logs():
    """A non-Update object (or no chat) means no one to reply to — just log."""
    context = MagicMock()
    context.error = ValueError("boom")
    context.bot = AsyncMock()

    with patch("src.bot.logging.error") as mock_log:
        await error_handler("not an update", context)

    mock_log.assert_called_once()
    context.bot.send_message.assert_not_called()


async def test_error_handler_survives_failed_notification():
    """If even the notification fails, the handler must not raise."""
    update = MagicMock(spec=Update)
    update.effective_chat = MagicMock()
    update.effective_chat.id = 42

    context = MagicMock()
    context.error = ValueError("boom")
    context.bot = AsyncMock()
    context.bot.send_message.side_effect = Exception("network down")

    with patch("src.bot.logging.error"):
        await error_handler(update, context)  # must not raise


# --- build_application ------------------------------------------------------


def test_build_application_wires_everything():
    with patch("src.bot.ApplicationBuilder") as mock_builder:
        mock_app = MagicMock()
        mock_builder.return_value.token.return_value.post_init.return_value.build.return_value = mock_app

        result = build_application()

    assert result is mock_app
    assert mock_app.add_handler.call_count >= 4
    mock_app.add_error_handler.assert_called_once()
