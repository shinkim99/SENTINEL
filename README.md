# SENTINEL — 주간 규제 인텔리전스 에이전트

4개 R&D 도메인(2차전지·친환경·수소·우주환경)의 법규/규제/정책 변화를 주 1회 수집·분석하여 R&D 본부에 발송하는 서비스.

## 빠른 시작

```bash
pip install -r requirements.txt
cp .env.example .env   # 값 채우기
uvicorn app.main:app --reload --port 8010
```

## 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/health` | 헬스체크 |
| POST | `/digest/run` | 주간 다이제스트 실행 |

## 디렉토리 구조

```
app/
  main.py          FastAPI 진입점
  models.py        Pydantic 데이터 모델
  collect/         도메인별 수집기 (구현 예정)
  screen.py        1차/2차 스크리닝 (구현 예정)
  diff.py          dedup + Weekly State 비교 (구현 예정)
  synthesize.py    HTML 생성 (구현 예정)
  deliver.py       로컬 발송 테스트용 (구현 예정)
data/
  profiles/        도메인 프로파일 JSON 4종
  sources.json     Source Registry
  state/           주간 스냅샷 (gitignore)
n8n/               워크플로우 export (참고용)
```

## 두 가지 실행 경로

### A. 개인용 (n8n + FastAPI 서버) — 기존, 보존
`uvicorn app.main:app` + n8n Cron/SMTP. 서버 노트북에서 상시 구동. `app/main.py`·`n8n/` 그대로 유지.

### B. 서버리스 (GitHub Actions + Resend + Pages) — 신규
서버·Docker·n8n 없이 GitHub Actions만으로 주간 발송 + 대시보드 배포.

```bash
# 로컬 수동 실행 (draft = 본인 첫 수신자에게만 검토용)
python -m scripts.run_digest --mode draft
# 본부 전체 발송
python -m scripts.run_digest --mode send
```

- 파이프라인 함수(collect/screen/diff/synthesize/registry)는 A와 100% 공유. 바뀌는 건 진입점·발송·스케줄·배포뿐.
- **검토 게이트(review_first의 GitHub판)**: 매주 schedule은 자동으로 **draft(운영자 본인)**만 발송하고 Pages 대시보드를 갱신한다. **본부 발송은 Actions 탭 → Run workflow → mode=send 수동 트리거**로만 이뤄진다. → *초안 확인 → 수동 send* 흐름.
- `registry.json`(diff 기준선)은 매 실행 후 Actions가 레포에 commit & push 하여 다음 주 "신규 변경"만 추출.
- 같은 주에 draft 실행 후 send를 또 실행하면 baseline이 이미 갱신되어 변경분이 비어 있을 수 있다. 본부 발송이 필요한 주에는 schedule을 기다리지 말고 바로 mode=send를 실행한다.

#### 필요한 GitHub 설정
- **Secrets** (Settings → Secrets and variables → Actions): `ANTHROPIC_API_KEY`, `LAW_GO_KR_API_KEY`, `DIGEST_RECIPIENTS`, `RESEND_API_KEY`
- **Pages** (Settings → Pages): Source = "GitHub Actions"
- 대시보드 공개 URL: `https://shinkim99.github.io/SENTINEL/` (워크플로우 env `DASHBOARD_URL`)

#### 첫 수동 실행
GitHub → **Actions** 탭 → **SENTINEL Weekly Digest** → **Run workflow** → mode 선택(draft 권장) → 실행.
완료 후 본인 메일함에 v2 이메일, Pages에 v4 대시보드가 뜬다.

## 설정

`.env.example`을 참고하여 `.env`를 작성. `.env`는 커밋하지 않는다.
서버리스 경로는 같은 키들을 GitHub Secrets로 주입한다(코드/로그 노출 금지).
