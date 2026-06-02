# SENTINEL 서버 배포 절차 (n8n + reg-watch)

> 이 문서는 **사람이 서버에서 직접 실행**하는 절차서다. Claude는 파일만 생성한다.
> 단일 진실원천(single source of truth)은 레포 루트의 `CLAUDE.md`. 본 문서는 그 배포 절차일 뿐이며,
> 정책/아키텍처가 충돌하면 항상 `CLAUDE.md`를 따른다.

## 0. 배치 위치

배포 키트를 레포 루트에 둔다. (reg-watch 소스 = `app/`, 정적 데이터 = `data/`)

```
SENTINEL/
├── CLAUDE.md
├── Dockerfile                       # reg-watch 이미지
├── docker-compose.yml               # reg-watch + n8n 공동 운영
├── .env                             # ← .env.example 복사 후 채움 (git 커밋 금지)
├── .env.example                     # 비밀 항목 템플릿
├── requirements.txt                 # (reg-watch 의존성 — 기존 레포 것 사용)
├── app/                             # FastAPI 소스 (app.main:app)
├── data/                            # profiles/, sources.json, state/
└── n8n/
    ├── sentinel_weekly_digest.json  # n8n Import용 워크플로우
    └── README_n8n.md                # 이 문서
```

## 1. `.env` 준비

`.env.example`을 복사해 `.env`를 만들고 값을 채운다. **모든 비밀은 여기서만** 관리한다.

```bash
cp .env.example .env
nano .env
```

채워야 할 핵심 키:

- `ANTHROPIC_API_KEY` — 주 모델 키 (필수)
- `OPENAI_API_KEY`, `GEMINI_API_KEY` — fallback (선택)
- `SEND_MODE=review_first` — **초기값 유지** (운영자 사전 승인 후 발송)
- `DIGEST_RECIPIENTS` — R&D 본부 수신 주소(쉼표 구분). 회사 실명/도메인 하드코딩은 코드/compose가 아닌 `.env`에만.
- `OPERATOR_EMAIL` — 검토 메일을 받을 운영자(본인) 주소
- `DIGEST_FROM_EMAIL` — 발신 주소
- `DASHBOARD_URL=http://<SERVER_HOST>:8010/dashboard` — 운영 대시보드 링크 (`<SERVER_HOST>` 교체)

> **워크플로우 ↔ reg-watch 계약**: `n8n` 워크플로우는 수신자/발신자/운영자 주소를
> `/digest/run` 응답 JSON에서 읽는다(`recipients`, `from_email`, `operator_email`).
> 즉 reg-watch가 `.env`의 위 값들을 응답에 실어 보내야 n8n에 주소를 하드코딩하지 않아도 된다.
> reg-watch가 아직 이 필드를 내보내지 않으면, import 후 n8n UI에서 해당 노드의 To/From을 직접 입력한다.

## 2. `docker-compose.yml` 자리표시자 교체

`docker-compose.yml`의 `<SERVER_HOST>`를 **서버의 외부 접속 IP/도메인**으로 바꾼다.
이 값은 n8n `WEBHOOK_URL`에 쓰이며, **승인 메일의 Approve/Disapprove 링크가 작동하려면 필수**다.

```yaml
- WEBHOOK_URL=http://10.0.0.5:5678/      # 예시
```

## 3. 빌드 및 기동

```bash
docker compose up -d --build
docker compose ps           # 두 컨테이너 healthy 확인
docker compose logs -f reg-watch
```

- reg-watch는 내부 네트워크에서 `http://reg-watch:8010`으로만 노출(기본). 호스트에서 직접 보려면 compose의 `ports` 주석 해제.
- n8n은 `http://<SERVER_HOST>:5678`로 접속.

## 4. n8n 워크플로우 Import 및 자격증명 바인딩

1. 브라우저에서 `http://<SERVER_HOST>:5678` 접속 → 로그인.
2. **Workflows → Import from File** → `n8n/sentinel_weekly_digest.json` 선택.
3. **SMTP 자격증명 바인딩** (JSON에는 자격증명이 들어있지 않다 — 의도된 것):
   - `승인대기`, `본부발송`, `본부발송(직행)` 세 노드 모두 SMTP 자격증명이 비어 있다.
   - **기존 AI뉴스/주식 발송 워크플로우의 SMTP credential을 그대로 재선택**하면 된다.
4. **주소 확인**:
   - reg-watch가 `recipients`/`from_email`/`operator_email`을 응답에 싣는 경우 → 표현식 그대로 두면 됨.
   - 아니면 → `승인대기`의 To(운영자 본인), `본부발송`/`본부발송(직행)`의 To(운영 수신자)·From을 직접 입력.
5. **`WEBHOOK_URL` 확인**: 좌하단 또는 Settings에서 n8n이 인식한 webhook 기준 URL이 서버 주소(`http://<SERVER_HOST>:5678/`)인지 확인. localhost로 잡혀 있으면 승인 링크가 작동하지 않는다.

## 5. 수동 1회 테스트 (Activate 전)

1. 워크플로우 열고 **Execute Workflow** 실행.
2. `생성` 노드가 `http://reg-watch:8010/digest/run`을 호출 → `status`, `digest_id`, `html` 수신 확인.
3. `status == "pending_review"`이면 → **검토 메일 수신** 확인 (운영자 주소).
4. 메일의 **Approve** 클릭 → `승인됨?` 통과 → `승인확정`이 `/digest/{digest_id}/approve` 호출 → `본부발송`이 SMTP로 발송.
5. **Disapprove** 시 → `승인됨?`에서 막혀 발송되지 않음(검토 게이트 동작 확인).
6. `status == "ready_to_send"`(auto_send 모드)면 → `본부발송(직행)`으로 바로 발송.

## 6. 활성화 (Cron 가동)

정상 동작을 확인하면 우상단 토글로 **Activate**. 이후 매주 **월요일 07:00(Asia/Seoul)** 자동 실행.

- Cron: `0 7 * * 1`
- 타임존은 compose의 `GENERIC_TIMEZONE/TZ=Asia/Seoul` 및 워크플로우 settings에서 보장.

## 7. 발송 모드 전환 (신뢰 누적 후)

- 초기: `SEND_MODE=review_first` 유지 → 매주 운영자 승인 게이트 통과.
- **2~3주 안정 운영 후** `auto_send`로 전환:
  ```bash
  # .env 만 교체
  sed -i 's/^SEND_MODE=review_first/SEND_MODE=auto_send/' .env
  docker compose up -d        # reg-watch 재기동 (재빌드 불필요)
  ```
- auto_send에서는 reg-watch가 `status: "ready_to_send"`를 반환 → 워크플로우가 검토 메일 없이 `본부발송(직행)`으로 직행.

## 8. 운영 메모

- **로그**: `docker compose logs -f reg-watch` / n8n 실행 이력은 UI Executions 탭.
- **상태 영속화**: 주간 스냅샷/레지스트리 이력은 `regwatch_state` 볼륨에 보존(다음 주 diff 기준). 볼륨 삭제 금지.
- **수집 0건**: "변화 없음"이 아니라 "수집 실패"일 수 있음(CLAUDE.md §4.6). reg-watch health alert를 확인.
- **시크릿 위치**: API 키·수신자는 오직 `.env`. SMTP 자격증명은 오직 n8n UI. JSON/compose/Dockerfile 어디에도 두지 않는다.
