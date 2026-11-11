import logging

import pytest

from src.logging_config import setup_logging


@pytest.fixture
def fresh_setup_logging():
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level

    def _setup():
        root.handlers.clear()
        setup_logging()
        return root

    yield _setup
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_info_goes_to_stdout_and_warning_to_stderr(fresh_setup_logging, capsys):
    fresh_setup_logging()
    logger = logging.getLogger("test_logging_config")

    logger.info("normal progress message")
    logger.warning("something to look at")
    logger.error("something failed")

    captured = capsys.readouterr()
    assert "normal progress message" in captured.out
    assert "normal progress message" not in captured.err
    assert "something to look at" in captured.err
    assert "something to look at" not in captured.out
    assert "something failed" in captured.err


def test_debug_is_hidden_at_info_level(fresh_setup_logging, capsys):
    fresh_setup_logging()
    logging.getLogger("test_logging_config").debug("noisy detail")

    captured = capsys.readouterr()
    assert "noisy detail" not in captured.out
    assert "noisy detail" not in captured.err


def test_does_not_add_handlers_twice(fresh_setup_logging):
    root = fresh_setup_logging()
    setup_logging()

    assert len(root.handlers) == 2
