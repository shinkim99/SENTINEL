@echo off
:: SENTINEL — KR 규제 수집 + git push 자동화
:: 회사 PC에서 실행. 작업 스케줄러에 등록하거나 직접 더블클릭.
:: 전제: git 설치, 시스템 python 설치, .env 에 LAW_GO_KR_API_KEY 설정.
:: venv 불필요 — 시스템 python 직접 사용.

setlocal

:: 스크립트 위치 기준으로 레포 루트로 이동 (scripts\ 의 상위)
cd /d "%~dp0.."

echo.
echo ============================================================
echo  SENTINEL KR 수집  ^|  %DATE% %TIME%
echo ============================================================

:: 1. KR 수집 (모듈 실행 — 프로젝트 루트에서 실행해야 app 패키지 인식)
echo.
echo [1/4] python -m scripts.collect_kr ...
python -m scripts.collect_kr
if %errorlevel% neq 0 (
    echo.
    echo [오류] KR 수집 실패 ^(exitcode=%errorlevel%^) -- git push 건너뜀
    exit /b 1
)

:: 2. 원격 최신화
echo.
echo [2/4] git pull --rebase ...
git pull --rebase
if %errorlevel% neq 0 (
    echo [오류] git pull 실패
    exit /b 1
)

:: 3. 변경 확인 후 커밋
echo.
echo [3/4] staging kr_latest.json ...
git add data\inbox\kr_latest.json

git diff --staged --quiet
if %errorlevel% equ 0 (
    echo [알림] kr_latest.json 변경 없음 -- 커밋/push 건너뜀
    echo        ^(이번 주 이미 업로드된 내용과 동일합니다^)
    exit /b 0
)

for /f %%i in ('python -c "import datetime; d=datetime.date.today().isocalendar(); print(str(d[0])+'-W'+str(d[1]).zfill(2))"') do set WEEK=%%i

echo commit: chore: KR collection %WEEK%
git commit -m "chore: KR collection %WEEK%"
if %errorlevel% neq 0 (
    echo [오류] git commit 실패
    exit /b 1
)

:: 4. Push → GitHub Actions 자동 트리거
echo.
echo [4/4] git push ...
git push
if %errorlevel% neq 0 (
    echo [오류] git push 실패
    exit /b 1
)

echo.
echo ============================================================
echo  완료. GitHub Actions 가 자동으로 다이제스트를 실행합니다.
echo  진행 상황: https://github.com/shinkim99/SENTINEL/actions
echo ============================================================
exit /b 0
