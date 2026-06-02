# SENTINEL reg-watch (FastAPI) — production image
FROM python:3.12-slim

# 회사망 SSL inspection 환경 대응: 빌드 시 사내 CA가 필요하면
# certs/ 디렉터리에 PEM을 넣고 아래 두 줄의 주석을 해제한다.
# COPY certs/ /usr/local/share/ca-certificates/
# RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
#     && update-ca-certificates && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 의존성 레이어 분리 (소스 변경 시 캐시 재사용)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 소스
COPY app/ ./app/
# 프로파일/소스 레지스트리 등 정적 데이터 (state는 volume으로 덮어씀)
COPY data/ ./data/

# 상태 영속화 디렉터리 (docker-compose volume이 마운트됨)
RUN mkdir -p /app/data/state

EXPOSE 8010

# 단순 헬스 핀 (reg-watch에 /health가 있다고 가정; 없으면 compose의 healthcheck도 함께 제거)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8010/health', timeout=3).status==200 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
