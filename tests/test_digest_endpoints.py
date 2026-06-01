"""FastAPI 엔드포인트 통합 테스트 — /digest/run, /approve, /dashboard.

실제 API 호출 없이 collect/screen을 fixture로 대체하여 엔드포인트 흐름을 검증한다.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from tests.conftest import REPO_ROOT

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_settings(state_dir: Path) -> Settings:
    """tmp state_dir를 사용하는 테스트용 Settings."""
    return Settings(
        anthropic_api_key="test-key",
        state_dir=state_dir,
        profiles_dir=REPO_ROOT / "data" / "profiles",
        sources_path=REPO_ROOT / "data" / "sources.json",
        dashboard_url="http://localhost:8010/dashboard",
        send_mode="review_first",
        digest_recipients="test@example.com",
    )


_COLLECT_STATS = {
    "total_collected": 3,
    "by_source": {"eu-eurlex": {"count": 2, "status": "ok"}},
    "collection_failures": [],
}
_PASS1_STATS = {"total_screen1": 3, "passed_screen1": 3}
_PASS2_STATS = {
    "total_screen2": 3,
    "passed_screen2": 3,
    "dropped_citation_mismatch": 0,
    "dropped_not_relevant": 0,
}


def _run_digest(state_dir: Path, screened_items, digest_id: str = "2026-W22"):
    """실제 collect/screen 없이 /digest/run 호출 헬퍼. (review_first 모드)"""
    settings = _make_settings(state_dir)
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main._current_digest_id", return_value=digest_id),
        patch("app.main.collect_all", new=AsyncMock(return_value=([], _COLLECT_STATS))),
        patch("app.main.screen_pass1", new=AsyncMock(return_value=([], _PASS1_STATS))),
        patch(
            "app.main.screen_pass2",
            new=AsyncMock(return_value=(screened_items, _PASS2_STATS)),
        ),
    ):
        client = TestClient(app)
        return client.post("/digest/run"), client, settings


def _approve_digest(state_dir: Path, digest_id: str = "2026-W22"):
    """실제 SMTP 없이 /approve 호출 헬퍼."""
    settings = _make_settings(state_dir)
    with patch("app.main.get_settings", return_value=settings):
        client = TestClient(app)
        return client.post(f"/digest/{digest_id}/approve"), client


def _get_dashboard(state_dir: Path):
    settings = _make_settings(state_dir)
    with patch("app.main.get_settings", return_value=settings):
        client = TestClient(app)
        return client.get("/dashboard")


# ── Scenario 1: 1회차 엔드포인트 ─────────────────────────────────────────────

class TestEndpointFirstRun:
    def test_run_returns_200(self, tmp_path, sample_items):
        resp, _, _ = _run_digest(tmp_path, sample_items)
        assert resp.status_code == 200

    def test_run_status_pending_review(self, tmp_path, sample_items):
        resp, _, _ = _run_digest(tmp_path, sample_items)
        assert resp.json()["status"] == "pending_review"

    def test_run_reports_changed_count(self, tmp_path, sample_items):
        resp, _, _ = _run_digest(tmp_path, sample_items)
        data = resp.json()
        assert data["stats"]["changed_this_week"] == 3, (
            f"Expected 3 changed, got {data['stats']}"
        )

    def test_run_does_not_commit_registry(self, tmp_path, sample_items):
        """/digest/run 후 registry.json이 존재하면 안 된다 (미승인 상태)."""
        _run_digest(tmp_path, sample_items)
        assert not (tmp_path / "registry.json").exists(), (
            "registry.json must NOT exist before /approve"
        )

    def test_run_saves_pending_files(self, tmp_path, sample_items):
        """pending HTML + registry JSON 저장 확인."""
        _run_digest(tmp_path, sample_items)
        assert (tmp_path / "pending" / "2026-W22.html").exists()
        assert (tmp_path / "pending" / "2026-W22.registry.json").exists()

    def test_run_html_contains_dashboard_button(self, tmp_path, sample_items):
        """이메일 HTML에 대시보드 버튼이 포함된다."""
        resp, _, _ = _run_digest(tmp_path, sample_items)
        html = resp.json()["html"]
        assert "전체 레이더 보기" in html
        assert "http://localhost:8010/dashboard" in html


# ── Scenario 2: approve 후 baseline 커밋 ────────────────────────────────────

class TestEndpointApprove:
    def test_approve_commits_registry(self, tmp_path, sample_items):
        """/approve 후 registry.json 생성 확인."""
        _run_digest(tmp_path, sample_items)
        resp, _ = _approve_digest(tmp_path)

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        assert (tmp_path / "registry.json").exists(), (
            "registry.json must exist after /approve"
        )

    def test_approve_creates_audit_log(self, tmp_path, sample_items):
        """sent/{id}.json audit log 생성 확인."""
        _run_digest(tmp_path, sample_items)
        _approve_digest(tmp_path)

        assert (tmp_path / "sent" / "2026-W22.json").exists()

    def test_approve_returns_html(self, tmp_path, sample_items):
        """/approve 응답에 이메일 HTML 포함."""
        _run_digest(tmp_path, sample_items)
        resp, _ = _approve_digest(tmp_path)

        html = resp.json()["html"]
        assert html.startswith("<!DOCTYPE html")

    def test_approve_404_for_unknown_digest(self, tmp_path):
        """존재하지 않는 digest_id → 404."""
        settings = _make_settings(tmp_path)
        with patch("app.main.get_settings", return_value=settings):
            client = TestClient(app)
            resp = client.post("/digest/nonexistent-id/approve")
        assert resp.status_code == 404


# ── Scenario 3: 2회차 동일 입력 후 메일<대시보드 ─────────────────────────────

class TestEndpointSecondRunNoChanges:
    @pytest.fixture(autouse=True)
    def _week1(self, tmp_path, sample_items):
        """W22 run + approve → baseline 수립."""
        _run_digest(tmp_path, sample_items, "2026-W22")
        _approve_digest(tmp_path, "2026-W22")
        self.state_dir = tmp_path
        self.sample_items = sample_items

    def test_second_run_zero_changed(self):
        """2회차 동일 입력 → changed_this_week=0."""
        resp, _, _ = _run_digest(self.state_dir, self.sample_items, "2026-W23")
        data = resp.json()
        print(f"\n[Scenario 3 endpoint] stats: {data['stats']}")
        assert data["stats"]["changed_this_week"] == 0

    def test_second_run_email_shows_zero(self):
        """2회차 이메일 HTML에 "0" 카운트 표시."""
        resp, _, _ = _run_digest(self.state_dir, self.sample_items, "2026-W23")
        html = resp.json()["html"]
        assert ">0<" in html

    def test_dashboard_still_has_all_items(self):
        """대시보드 GET /dashboard → 3건 모두 표시."""
        resp = _get_dashboard(self.state_dir)
        assert resp.status_code == 200

        html = resp.text
        assert "eu-battery-regulation-2023-1542_EU" in html
        assert "ira-section-45x-manufacturing-credit_US" in html
        assert "hydrogen-safety-act-amendment_KR" in html

    def test_email_item_count_less_than_dashboard(self):
        """이메일 항목 수(0) < 대시보드 항목 수(3) 검증."""
        run_resp, _, _ = _run_digest(self.state_dir, self.sample_items, "2026-W23")
        dash_resp = _get_dashboard(self.state_dir)

        email_changed = run_resp.json()["stats"]["changed_this_week"]
        # Dashboard REG array contains all committed regulations
        import re
        reg_count = len(re.findall(r'"regulation_id":', dash_resp.text))

        print(f"\n[Scenario 3 endpoint] Email: {email_changed} / Dashboard: {reg_count}")
        assert email_changed < reg_count, (
            f"Email({email_changed}) must be < Dashboard({reg_count})"
        )


# ── Scenario 4: lifecycle 변경 → 해당 항목만 이메일 포함 ────────────────────

class TestEndpointLifecycleChange:
    @pytest.fixture(autouse=True)
    def _week1(self, tmp_path, sample_items):
        """W22 run + approve (proposed baseline)."""
        _run_digest(tmp_path, sample_items, "2026-W22")
        _approve_digest(tmp_path, "2026-W22")
        self.state_dir = tmp_path
        self.sample_items = sample_items

    def test_changed_item_in_email(self):
        """lifecycle proposed→enacted → 해당 항목 이메일 포함."""
        items_w23 = [
            self.sample_items[0].model_copy(update={"lifecycle_stage": "enacted"}),
            *self.sample_items[1:],
        ]
        resp, _, _ = _run_digest(self.state_dir, items_w23, "2026-W23")
        data = resp.json()

        print(f"\n[Scenario 4 endpoint] changed_this_week: {data['stats']['changed_this_week']}")
        assert data["stats"]["changed_this_week"] == 1

        html = data["html"]
        assert "EU Battery Regulation" in html
        # 변경 없는 항목은 이메일에 없어야 함
        assert "IRA Section 45X" not in html

    def test_history_persists_after_approve(self):
        """approve 후 registry.json에 history 2건 기록."""
        items_w23 = [
            self.sample_items[0].model_copy(update={"lifecycle_stage": "enacted"}),
            *self.sample_items[1:],
        ]
        _run_digest(self.state_dir, items_w23, "2026-W23")
        _approve_digest(self.state_dir, "2026-W23")

        from app.registry import load_registry
        registry = load_registry(self.state_dir)
        reg = registry["eu-battery-regulation-2023-1542_EU"]

        print(f"\n[Scenario 4 endpoint] history: {[h.note for h in reg.history]}")
        assert len(reg.history) == 2
        assert reg.lifecycle_stage == "enacted"

    def test_dashboard_shows_new_lifecycle_badge(self):
        """대시보드에 새 lifecycle stage 배지 표시."""
        items_w23 = [
            self.sample_items[0].model_copy(update={"lifecycle_stage": "enacted"}),
            *self.sample_items[1:],
        ]
        _run_digest(self.state_dir, items_w23, "2026-W23")
        _approve_digest(self.state_dir, "2026-W23")

        resp = _get_dashboard(self.state_dir)
        html = resp.text
        # "enacted" 값이 JSON에 있어야 함
        assert '"enacted"' in html


# ── Health check ──────────────────────────────────────────────────────────────

def test_health():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
