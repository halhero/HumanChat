from unittest.mock import Mock

from human_chat.cli.app import _choose_session
from human_chat.session_models import SessionRecord


def test_choose_recent_session_reuses_listed_record(monkeypatch):
    session = SessionRecord.create()
    repository = Mock()
    repository.list_recent.return_value = [session]
    monkeypatch.setattr("builtins.input", lambda _: "2")

    selected = _choose_session(repository)

    assert selected is session
    repository.load.assert_not_called()


def test_choose_session_from_list_reuses_selected_record(monkeypatch):
    sessions = [SessionRecord.create(), SessionRecord.create()]
    repository = Mock()
    repository.list_recent.return_value = sessions
    choices = iter(["3", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(choices))

    selected = _choose_session(repository)

    assert selected is sessions[1]
    repository.load.assert_not_called()
