"""레지스트리 diff 로직 검증 — 4개 시나리오 (단위 테스트).

Scenario 1: 1회차 (빈 레지스트리) → 모든 항목 changed_this_week=True
Scenario 2: pending 저장 후 baseline 미커밋 확인, approve 후 커밋 확인
Scenario 3: 2회차 (동일 입력) → changed_this_week=False; 이메일<대시보드
Scenario 4: lifecycle 변경 → 해당 항목만 changed_this_week=True + history 누적
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import ScreenedItem
from app.registry import (
    apply_screened_items,
    commit_registry,
    get_changed_items,
    load_registry,
    save_pending_registry,
)
from app.synthesize import build_dashboard, build_email


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1 — 1회차: 빈 레지스트리 → 전원 changed_this_week=True
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario1FirstRun:
    def test_all_items_marked_changed(self, sample_items):
        """신규 항목은 전부 changed_this_week=True."""
        updated, changed_ids = apply_screened_items(sample_items, {}, "2026-06-02")

        assert len(changed_ids) == 3, f"Expected 3, got {len(changed_ids)}: {changed_ids}"
        assert all(r.changed_this_week for r in updated.values()), (
            "All new items must be changed_this_week=True"
        )

    def test_initial_history_entry(self, sample_items):
        """신규 항목 history에 '신규 등록' 1건만 있어야 한다."""
        updated, _ = apply_screened_items(sample_items, {}, "2026-06-02")

        for reg in updated.values():
            assert len(reg.history) == 1, (
                f"{reg.regulation_id}: history len {len(reg.history)}, expected 1"
            )
            assert reg.history[0].note == "신규 등록"

    def test_regulation_ids_built_from_canonical_key(self, sample_items):
        """regulation_id = canonical_key + '_' + country."""
        updated, _ = apply_screened_items(sample_items, {}, "2026-06-02")

        assert "eu-battery-regulation-2023-1542_EU" in updated
        assert "ira-section-45x-manufacturing-credit_US" in updated
        assert "hydrogen-safety-act-amendment_KR" in updated

    def test_get_changed_items_matches_all(self, sample_items):
        """get_changed_items 결과가 apply 결과와 일치."""
        updated, changed_ids = apply_screened_items(sample_items, {}, "2026-06-02")
        changed = get_changed_items(updated)

        assert len(changed) == len(changed_ids) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2 — pending 저장 및 baseline 커밋 타이밍 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario2PendingAndCommit:
    def test_baseline_not_committed_before_approve(self, tmp_path, sample_items):
        """apply + save_pending 후 registry.json이 존재하면 안 된다."""
        updated, _ = apply_screened_items(sample_items, {}, "2026-06-02")
        save_pending_registry(updated, "2026-W22", tmp_path)

        assert not (tmp_path / "registry.json").exists(), (
            "registry.json must NOT exist before approval"
        )
        assert (tmp_path / "pending" / "2026-W22.registry.json").exists(), (
            "pending registry must be saved"
        )

    def test_pending_html_exists_alongside_registry(self, tmp_path, sample_items):
        """pending/*.html 은 별도로 저장된다 (registry와 무관)."""
        (tmp_path / "pending").mkdir(parents=True, exist_ok=True)
        (tmp_path / "pending" / "2026-W22.html").write_text("<html/>", encoding="utf-8")

        updated, _ = apply_screened_items(sample_items, {}, "2026-06-02")
        save_pending_registry(updated, "2026-W22", tmp_path)

        assert (tmp_path / "pending" / "2026-W22.html").exists()
        assert (tmp_path / "pending" / "2026-W22.registry.json").exists()

    def test_commit_writes_registry_and_audit_log(self, tmp_path, sample_items):
        """commit_registry → registry.json + sent/{id}.json 모두 생성."""
        updated, _ = apply_screened_items(sample_items, {}, "2026-06-02")
        commit_registry(updated, "2026-W22", tmp_path)

        reg_path = tmp_path / "registry.json"
        audit_path = tmp_path / "sent" / "2026-W22.json"
        assert reg_path.exists(), "registry.json must exist after commit"
        assert audit_path.exists(), "sent/{id}.json must exist after commit"

    def test_committed_registry_reloadable(self, tmp_path, sample_items):
        """커밋된 registry.json을 재로드하면 동일 항목 수를 반환한다."""
        updated, _ = apply_screened_items(sample_items, {}, "2026-06-02")
        commit_registry(updated, "2026-W22", tmp_path)

        reloaded = load_registry(tmp_path)
        assert len(reloaded) == 3

    def test_audit_log_changed_count(self, tmp_path, sample_items):
        """audit log의 changed_this_week 카운트가 실제 변경 수와 일치."""
        updated, changed_ids = apply_screened_items(sample_items, {}, "2026-06-02")
        commit_registry(updated, "2026-W22", tmp_path)

        audit = json.loads((tmp_path / "sent" / "2026-W22.json").read_text(encoding="utf-8"))
        assert audit["changed_this_week"] == len(changed_ids)
        assert audit["total_regulations"] == len(updated)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3 — 2회차 동일 입력 → changed_this_week=False; 메일<대시보드
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario3SecondRunNoChanges:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, sample_items):
        """1회차 run + approve 로 baseline 수립."""
        updated1, _ = apply_screened_items(sample_items, {}, "2026-06-02")
        commit_registry(updated1, "2026-W22", tmp_path)
        self.state_dir = tmp_path
        self.sample_items = sample_items

    def test_second_run_no_changed_items(self):
        """2회차 동일 입력 → changed_this_week=False 전원."""
        baseline = load_registry(self.state_dir)
        updated2, changed_ids = apply_screened_items(
            self.sample_items, baseline, "2026-06-09"
        )
        changed = get_changed_items(updated2)

        print(f"\n[Scenario 3] 2nd run changed_ids: {changed_ids}")
        assert changed_ids == [], f"Expected [], got {changed_ids}"
        assert changed == []

    def test_email_has_zero_items(self):
        """2회차 이후 이메일에 포함될 항목이 0건."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.sample_items, baseline, "2026-06-09")
        changed = get_changed_items(updated2)

        email_html = build_email(changed, [], "http://localhost:8010/dashboard")
        # Metric card shows "0"
        assert ">0<" in email_html, "Email should show 0 changed items"
        print(f"\n[Scenario 3] Email items: {len(changed)}")

    def test_dashboard_retains_all_items(self):
        """대시보드에는 변화 없는 항목도 모두 표시된다."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.sample_items, baseline, "2026-06-09")

        dashboard_html = build_dashboard(list(updated2.values()))
        all_ids = [
            "eu-battery-regulation-2023-1542_EU",
            "ira-section-45x-manufacturing-credit_US",
            "hydrogen-safety-act-amendment_KR",
        ]
        for reg_id in all_ids:
            assert reg_id in dashboard_html, f"Dashboard missing {reg_id}"

        print(f"\n[Scenario 3] Dashboard items: {len(updated2)}")

    def test_email_count_less_than_dashboard_count(self):
        """이메일 항목 수 < 대시보드 항목 수 (0 < 3)."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.sample_items, baseline, "2026-06-09")
        changed = get_changed_items(updated2)

        email_count = len(changed)
        dashboard_count = len(updated2)

        print(
            f"\n[Scenario 3] Email: {email_count} / Dashboard: {dashboard_count}"
        )
        assert email_count < dashboard_count, (
            f"Email({email_count}) must be < Dashboard({dashboard_count})"
        )

    def test_registry_preserves_all_items(self):
        """레지스트리 항목이 2회차 후에도 유지된다."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.sample_items, baseline, "2026-06-09")

        assert len(updated2) == 3, f"Registry should have 3 items, got {len(updated2)}"

    def test_second_run_does_not_duplicate_history(self):
        """2회차 실행 후 history가 중복 추가되지 않는다."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.sample_items, baseline, "2026-06-09")

        for reg in updated2.values():
            assert len(reg.history) == 1, (
                f"{reg.regulation_id}: history grew without state change"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4 — lifecycle 변경 → 해당 항목만 changed + history 누적
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario4LifecycleChange:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, sample_items):
        """1회차 run + approve (proposed → baseline)."""
        updated1, _ = apply_screened_items(sample_items, {}, "2026-06-02")
        commit_registry(updated1, "2026-W22", tmp_path)
        self.state_dir = tmp_path
        self.sample_items = sample_items

        # 2회차: EU 항목 lifecycle proposed → enacted
        self.items_week2 = [
            sample_items[0].model_copy(update={"lifecycle_stage": "enacted"}),
            sample_items[1],  # unchanged
            sample_items[2],  # unchanged
        ]

    def test_only_changed_item_flagged(self):
        """lifecycle 변경된 항목만 changed_this_week=True."""
        baseline = load_registry(self.state_dir)
        updated2, changed_ids = apply_screened_items(
            self.items_week2, baseline, "2026-06-09"
        )

        print(f"\n[Scenario 4] changed_ids: {changed_ids}")
        assert len(changed_ids) == 1
        assert "eu-battery-regulation-2023-1542_EU" in changed_ids

        assert updated2["eu-battery-regulation-2023-1542_EU"].changed_this_week is True
        assert updated2["ira-section-45x-manufacturing-credit_US"].changed_this_week is False
        assert updated2["hydrogen-safety-act-amendment_KR"].changed_this_week is False

    def test_history_appended_with_diff_note(self):
        """변경 history에 이전 stage 보존 및 diff 노트 포함."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.items_week2, baseline, "2026-06-09")

        reg = updated2["eu-battery-regulation-2023-1542_EU"]
        print(f"\n[Scenario 4] history: {[h.note for h in reg.history]}")

        assert len(reg.history) == 2, (
            f"Expected 2 history entries, got {len(reg.history)}"
        )
        # 최신 history에 이전→현재 stage 기록
        latest = reg.history[-1]
        assert "proposed" in latest.note, f"Expected 'proposed' in note: {latest.note}"
        assert "enacted" in latest.note, f"Expected 'enacted' in note: {latest.note}"

    def test_lifecycle_updated_in_registry(self):
        """레지스트리의 lifecycle_stage가 새 값으로 갱신된다."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.items_week2, baseline, "2026-06-09")

        reg = updated2["eu-battery-regulation-2023-1542_EU"]
        assert reg.lifecycle_stage == "enacted"

    def test_unchanged_items_history_not_grown(self):
        """변경 없는 항목은 history가 늘어나지 않는다."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.items_week2, baseline, "2026-06-09")

        for reg_id in [
            "ira-section-45x-manufacturing-credit_US",
            "hydrogen-safety-act-amendment_KR",
        ]:
            reg = updated2[reg_id]
            assert len(reg.history) == 1, (
                f"{reg_id}: history grew without state change"
            )

    def test_changed_item_appears_in_email(self):
        """lifecycle 변경 항목은 이메일에 포함된다."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.items_week2, baseline, "2026-06-09")
        changed = get_changed_items(updated2)

        email_html = build_email(changed, [], "http://localhost:8010/dashboard")

        assert "EU Battery Regulation" in email_html, "Changed item missing from email"
        assert "IRA Section 45X" not in email_html, "Unchanged item must not appear in email"

    def test_all_items_in_dashboard(self):
        """대시보드에는 변경/미변경 항목 모두 표시된다."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.items_week2, baseline, "2026-06-09")

        dashboard_html = build_dashboard(list(updated2.values()))

        assert "EU Battery Regulation" in dashboard_html
        assert "IRA Section 45X" in dashboard_html
        assert "수소안전관리법" in dashboard_html

    def test_history_week2_after_commit(self, tmp_path):
        """commit 후 reload해도 history가 유지된다."""
        baseline = load_registry(self.state_dir)
        updated2, _ = apply_screened_items(self.items_week2, baseline, "2026-06-09")
        commit_registry(updated2, "2026-W23", self.state_dir)

        reloaded = load_registry(self.state_dir)
        reg = reloaded["eu-battery-regulation-2023-1542_EU"]
        assert len(reg.history) == 2, "History must persist after commit"


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_canonical_key_fallback_from_title(self):
        """canonical_key 미제공 시 title에서 슬러그 파생."""
        from app.models import Citation
        item = ScreenedItem(
            source_id="src", title="EU CBAM Phase-II Rule 2025",
            url="https://x.com/1", published_at="2026-01-01",
            snippet="...", country="EU", domain="green_eco",
            lifecycle_stage="proposed", impact_summary="Carbon border adjustment",
            citation=Citation(source_id="src", quote="quote"),
            canonical_key="",  # 비어 있음
        )
        updated, changed_ids = apply_screened_items([item], {}, "2026-06-02")

        # regulation_id should be derived from title slug
        assert len(changed_ids) == 1
        reg_id = changed_ids[0]
        assert reg_id.endswith("_EU")
        assert "eu" in reg_id and "cbam" in reg_id, f"Unexpected slug: {reg_id}"

    def test_dedup_screened_removes_duplicate_urls(self, sample_items):
        """동일 URL 중복 입력은 dedup_screened로 제거된다."""
        from app.registry import dedup_screened

        duplicated = sample_items + [sample_items[0]]  # item[0] 중복
        result = dedup_screened(duplicated)
        assert len(result) == 3

    def test_empty_input_no_change(self, tmp_path, sample_items):
        """수집 0건 → 레지스트리 변화 없음 (changed_this_week 전부 False)."""
        updated1, _ = apply_screened_items(sample_items, {}, "2026-06-02")
        commit_registry(updated1, "2026-W22", tmp_path)

        baseline = load_registry(tmp_path)
        updated2, changed_ids = apply_screened_items([], baseline, "2026-06-09")

        assert changed_ids == []
        assert all(not r.changed_this_week for r in updated2.values())

    def test_multiple_commits_accumulate_history(self, tmp_path, sample_items):
        """3주 연속 실행: W22(신규) → W23(변경) → W24(변경없음) 후 history=3."""
        # W22: proposed
        updated1, _ = apply_screened_items(sample_items, {}, "2026-06-02")
        commit_registry(updated1, "2026-W22", tmp_path)

        # W23: enacted
        items_w23 = [
            sample_items[0].model_copy(update={"lifecycle_stage": "enacted"}),
            *sample_items[1:],
        ]
        baseline2 = load_registry(tmp_path)
        updated2, _ = apply_screened_items(items_w23, baseline2, "2026-06-09")
        commit_registry(updated2, "2026-W23", tmp_path)

        # W24: in_force
        items_w24 = [
            sample_items[0].model_copy(update={"lifecycle_stage": "in_force"}),
            *sample_items[1:],
        ]
        baseline3 = load_registry(tmp_path)
        updated3, _ = apply_screened_items(items_w24, baseline3, "2026-06-16")
        commit_registry(updated3, "2026-W24", tmp_path)

        final = load_registry(tmp_path)
        reg = final["eu-battery-regulation-2023-1542_EU"]
        print(f"\n[EdgeCase] history after 3 weeks: {[h.note for h in reg.history]}")
        assert len(reg.history) == 3
        assert reg.lifecycle_stage == "in_force"
