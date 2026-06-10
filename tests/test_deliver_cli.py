"""app.deliver CLI 발송 안전장치 검증.

- --test  : recipients_list[:1] (1명)에게만 발송. 2명 이상 절대 불가.
- --send  : 전체 발송 전, 표준입력으로 정확히 'yes'를 받아야 진행 (--yes로 생략 가능).
            확인 없으면 send_via_resend 자체가 호출되지 않아야 한다.
- 인자 없음: 도움말만, 발송 없음.

Resend API 실호출 없이 send_via_resend / _build_test_digest 를 monkeypatch 한다.
"""
from __future__ import annotations

import pytest

import app.config as config_module
import app.deliver as deliver


class _FakeSettings:
    def __init__(self, recipients: list[str]):
        self.digest_recipients = ",".join(recipients)
        self.resend_api_key = "re_test"
        self.resend_from_email = "SENTINEL <sentinel@example.com>"
        self.digest_reply_to = "owner@example.com"
        self.dashboard_url = "https://example.com/"
        self.state_dir = "data/state"
        self.profiles_dir = "data/profiles"

    @property
    def recipients_list(self) -> list[str]:
        return [r.strip() for r in self.digest_recipients.split(",") if r.strip()]


@pytest.fixture
def fake_settings(monkeypatch):
    recipients = ["owner@example.com", "team-a@example.com", "team-b@example.com"]
    settings = _FakeSettings(recipients)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def fake_digest(monkeypatch):
    monkeypatch.setattr(
        deliver, "_build_test_digest",
        lambda cfg: ("2026-W24", "[SENTINEL] 주간 규제 다이제스트 — 2026-W24", "<html></html>"),
    )


@pytest.fixture
def spy_send(monkeypatch):
    calls: list[dict] = []

    def _fake_send(html, recipients, cfg, *, subject="", from_email="", reply_to=""):
        calls.append({"recipients": recipients, "subject": subject})
        return {"id": "fake-id"}

    monkeypatch.setattr(deliver, "send_via_resend", _fake_send)
    return calls


# ── --test : 1명에게만 ──────────────────────────────────────────────────────────

def test_test_flag_sends_to_first_recipient_only(fake_settings, fake_digest, spy_send, capsys):
    deliver.main(["--test"])

    assert len(spy_send) == 1
    assert spy_send[0]["recipients"] == ["owner@example.com"]

    out = capsys.readouterr().out
    assert "TEST 발송 → owner@example.com" in out


def test_test_flag_never_sends_to_multiple(fake_settings, fake_digest, spy_send):
    """recipients_list 에 3명이 있어도 --test는 항상 1명."""
    deliver.main(["--test"])
    assert all(len(c["recipients"]) == 1 for c in spy_send)


# ── --send : 확인 없으면 발송 안 됨 ──────────────────────────────────────────────

def test_send_without_confirmation_aborts(fake_settings, fake_digest, spy_send, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "no")

    deliver.main(["--send"])

    assert spy_send == []  # 아무것도 보내지 않음
    out = capsys.readouterr().out
    assert "발송 취소됨" in out
    assert "3명" in out  # 전체 인원수 사전 출력


def test_send_with_yes_input_proceeds(fake_settings, fake_digest, spy_send, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "yes")

    deliver.main(["--send"])

    assert len(spy_send) == 1
    assert spy_send[0]["recipients"] == fake_settings.recipients_list


def test_send_with_yes_flag_skips_prompt(fake_settings, fake_digest, spy_send, monkeypatch):
    def _no_input(_prompt=""):
        raise AssertionError("--yes 사용 시 input()을 호출하면 안 됨")

    monkeypatch.setattr("builtins.input", _no_input)

    deliver.main(["--send", "--yes"])

    assert len(spy_send) == 1
    assert spy_send[0]["recipients"] == fake_settings.recipients_list


def test_send_requires_exact_yes(fake_settings, fake_digest, spy_send, monkeypatch):
    """'Yes', 'y', 'YES' 등은 진행하지 않음 — 정확히 'yes'만."""
    monkeypatch.setattr("builtins.input", lambda _prompt="": "Yes")

    deliver.main(["--send"])

    assert spy_send == []


# ── 인자 없음: 도움말만, 발송 없음 ────────────────────────────────────────────────

def test_no_args_prints_help_and_does_not_send(fake_settings, fake_digest, spy_send, capsys):
    deliver.main([])

    assert spy_send == []
    out = capsys.readouterr().out
    assert "usage" in out.lower()


# ── --test/--send 동시 사용 불가 ─────────────────────────────────────────────────

def test_test_and_send_mutually_exclusive(fake_settings, fake_digest, spy_send):
    with pytest.raises(SystemExit):
        deliver.main(["--test", "--send"])
    assert spy_send == []
