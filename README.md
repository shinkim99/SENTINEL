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

## 수집기 / 진단

| 소스 | 방식 | GitHub Actions 접근성 |
|---|---|---|
| US Federal Register | REST API (공개, 인증 불필요) | 가능 (UA 헤더 추가, 3회 재시도) |
| EU EUR-Lex | **Cellar SPARQL** (Publications Office 공식 API) | 가능 (HTML 스크래핑 제거) |
| KR law.go.kr | REST API (OC 키 필요) | ⚠ **IP 바인딩 제약** (아래 참조) |

**law.go.kr OC 키 IP 바인딩**: 법제처 Open API OC 키는 발급 시 등록한 IP에서만 유효하다. GitHub Actions 러너는 실행마다 IP가 바뀌므로 접근이 거부된다. 증상: HTTP 401 또는 "등록되지 않은 인증키" 응답. 해결: 법제처 개발자센터에서 IP 제한 해제(화이트리스트) 신청. 해제 전까지는 Actions에서 KR 소스가 `collection_failure`로 기록되고 이메일에 수집 실패 알림이 표시된다.

```bash
# 수집기 진단 — 소스별 HTTP 상태/건수만 출력 (키 값 출력 금지)
python -m scripts.diag_collect           # 전체
python -m scripts.diag_collect --source eu   # EUR-Lex만
python -m scripts.diag_collect --source us   # Federal Register만
python -m scripts.diag_collect --source kr   # law.go.kr만
```

## 설정

`.env.example`을 참고하여 `.env`를 작성. `.env`는 커밋하지 않는다.
서버리스 경로는 같은 키들을 GitHub Secrets로 주입한다(코드/로그 노출 금지).
