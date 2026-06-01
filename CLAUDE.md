# SENTINEL — 주간 규제 인텔리전스 에이전트

> 작업 코드네임: SENTINEL (regulatory watch). 4개 R&D 도메인의 법규/규제/정책
> 변화를 주 1회 수집·요약하여 R&D 본부에 push mail로 송부한다.
> 기존 n8n(AI 뉴스·주식 일일 발송) 인프라 위에 얹는 구조.

---

## 1. 목적

- 대상 도메인 4종(각각 독립 프로젝트): **2차전지 · 친환경 · 수소 · 우주환경**
- 각 도메인별로 "어느 국가의 어떤 규제를, 어떤 관점에서" 추적할지를 Project Profile로 정의
- 매주 1회: 수집 → 관련성 필터 → 영향도 분석 → HTML 요약 → 발송
- 보고 품질의 핵심 가치는 "노이즈 제거 + 정확한 lifecycle 판정 + 프로젝트 관점의 영향 해석"

## 2. 아키텍처 — 하이브리드 (n8n + FastAPI)

경계 기준: **결정론적 I/O는 n8n, 반복·판단·상태 로직은 서비스.**

```
n8n (Cron 주1회)
  → POST /digest/run → reg-watch (FastAPI)
                          collect → 1차 screen → 2차 screen → dedup/diff → synthesize(HTML)
  ← { html, summary, stats } ←
사람 검토 게이트 (review_first)
  → n8n SMTP 발송 → R&D 본부 1통
  → 발송본을 Weekly State에 저장 (다음 주 diff 기준)
```

- **n8n 책임**: Cron 스케줄, SMTP 발송, credential 관리. (기존 워크플로우와 동일 인프라 재사용)
- **reg-watch 서비스 책임**: 수집, 2단계 스크리닝, dedup/diff, synthesis, HTML 생성
- 둘은 단 한 번의 HTTP 호출로만 결합. 경계가 얇아야 각자 독립적으로 테스트·교체 가능.

## 3. 기반 데이터 구조 (품질의 80%)

기능 코드보다 먼저 안정화할 것.

### (A) Project Profile — 도메인 4개, 각 1개씩
```json
{
  "project_id": "battery",
  "domain": "secondary_battery",
  "watch_countries": ["EU", "US", "KR", "CN"],
  "keywords": ["recycled content", "carbon footprint", "FEOC", "battery passport"],
  "concerns": ["원료 수급 규제", "재활용 의무비율", "보조금 적격성"],
  "lifecycle_interest": ["enacted", "amended", "proposed"]
}
```

### (B) Source Registry — 도메인 × 국가 출처
```json
{ "id": "eu-eurlex", "domain": "all", "country": "EU",
  "type": "api", "tier": 1, "url": "...", "health": "ok" }
```
- tier 1 = 1차 출처(관보·규제기관), tier 2 = 산업 매체
- **tier 2는 발견용으로만 사용. 보고서에 넣기 전 반드시 tier 1로 검증.**

### (C) Weekly State — 주간 스냅샷
- 매주 결과를 저장하여 "지난주 대비 신규 변화"만 diff로 추출
- 누적되면 규제 타임라인 DB가 됨 (audit log = 재사용 가능한 학습 데이터)

## 4. 파이프라인 처리 규칙

1. **Collect**: 도메인별 sub-collector 병렬 실행. tier 1 우선.
2. **1차 스크리닝**: 저비용 모델로 도메인·국가 매칭만 (high recall).
3. **2차 스크리닝**: 고비용 모델로 영향도 분석. 두 가지 강제 규칙:
   - `lifecycle_stage` 필수 분류 (proposed / 공포 / 시행 / 개정 / 폐지)
   - 원문 source item을 **citation으로 인용 못 하면 drop** (hallucination 차단)
4. **Dedup + diff**: 동일 변화 클러스터링 → Weekly State와 비교 → 신규만 통과.
5. **Synthesize**: 도메인 → 국가 → 영향도 순. **출력은 HTML 컴포넌트 직접 생성**
   (markdown 금지). 품질 기준은 v5 수동 리포트(metric card, lifecycle 배지, 국가 비교 테이블).
6. **Health check**: 소스 수집 0건은 "변화 없음"이 아니라 "수집 실패"로 구분, 운영자 alert.

## 5. 신뢰성 패턴

- **Pydantic strict validation + single-retry + clarification loop** — 모든 LLM 단계의 기본 패턴.
- 모델 ID는 정확한 현행 문자열로. 잘못된 suffix는 silent 404를 유발하므로 항상 검증.
- Gemini 등 rate-limit(429) 대응: backoff + 다른 provider fallback.

## 6. 발송 정책

- 수신: R&D 본부 1통 (broadcast).
- `send_mode` 플래그: `review_first`(운영자 사전 승인 후 발송) | `auto_send`.
  - **초기에는 `review_first`로 시작**, 신뢰 누적 후 `auto_send` 전환.

## 7. 기술 스택 / 레포 구조 (제안)

- Backend: FastAPI, Python. Orchestration: n8n (Docker, 서버 노트북).
- AI: Anthropic(주), OpenAI/Gemini(fallback).

```
SENTINEL/
├── CLAUDE.md
├── app/
│   ├── main.py            # FastAPI, POST /digest/run
│   ├── models.py          # Pydantic: ProfileSpec, SourceItem, DigestResult
│   ├── collect/           # 도메인별 수집기
│   ├── screen.py          # 1차/2차 스크리닝
│   ├── diff.py            # dedup + Weekly State 비교
│   ├── synthesize.py      # HTML 생성
│   └── deliver.py         # (n8n이 담당하나 로컬 테스트용)
├── data/
│   ├── profiles/          # battery.json, green.json, hydrogen.json, space.json
│   ├── sources.json       # Source Registry
│   └── state/             # 주간 스냅샷
└── n8n/                   # 워크플로우 export (참고용)
```

## 8. 개발 / 실행

```bash
uvicorn app.main:app --reload --port 8010
# 회사망(SSL inspection) 환경: NODE_EXTRA_CA_CERTS 환경변수 1회 설정으로
# Node 기반 도구(Claude Code CLI 등) SSL 일괄 해결. (OS 인증서 미사용 도구 대상)
```

## 9. 작업 규약

- 멀티파일 수정은 **완전 교체 파일**(diff patch 아님)로 제공.
- 집(D:\projects\) ↔ 회사(P:\projects\) 동기화는 GitHub. 원격 변경 후 `git pull`, 개발은 `uvicorn --reload`.
- 단계 경계마다 human checkpoint(승인 게이트) 유지.

## 10. 제약 (중요)

- **고용주(회사) 실명과 특정 기밀 표현을 산출물·코드·문서에 절대 포함하지 않는다.**
  업무 맥락은 "회사 / 사내 / R&D 본부" 같은 일반 표현으로만 지칭.
- 보고서 본문은 사실 기반 + 출처 인용. 규제 lifecycle 단계를 단정하지 말고 명시.

## 11. 로드맵 (~화요일)

- [월] 레포 생성, CLAUDE.md 배치, 담당자 검토 메일 발송
- [목 오전] 피드백 수신 → Profile 4종 + Source Registry 확정 (search 검증)
- [금] v0: n8n 루프 1회전, 실제 메일 포맷·relevance 검증
- [월 8일] 두뇌를 reg-watch FastAPI로 추출 (2단계 스크리닝 + synthesis)
- [화 9일] 통합 + 첫 정식 주간 다이제스트 발송
