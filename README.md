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

## 설정

`.env.example`을 참고하여 `.env`를 작성. `.env`는 커밋하지 않는다.
