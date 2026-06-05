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

:: 1. KR 수집 (kr_latest.json 갱신)
echo.
echo [1/5] python -m scripts.collect_kr ...
python -m scripts.collect_kr
if %errorlevel% neq 0 (
    echo.
    echo [오류] KR 수집 실패 ^(exitcode=%errorlevel%^) -- git push 건너뜀
    exit /b 1
)

:: 2. 수집 결과 stage (pull 전에 작업트리를 깨끗하게 만들어야 rebase 가능)
echo.
echo [2/5] git add data\inbox\kr_latest.json ...
git add data\inbox\kr_latest.json

:: 3. 변경 있을 때만 커밋 (동일 결과면 스킵)
echo.
echo [3/5] git commit ^(변경 없으면 스킵^) ...
git diff --staged --quiet
if %errorlevel% equ 0 (
    echo [알림] kr_latest.json 변경 없음 -- 커밋 스킵
) else (
    for /f %%i in ('python -c "import datetime; d=datetime.date.today().isocalendar(); print(str(d[0])+'-W'+str(d[1]).zfill(2))"') do set WEEK=%%i
    echo commit: chore: KR collection %WEEK%
    git commit -m "chore: KR collection %WEEK%"
    if %errorlevel% neq 0 (
        echo [오류] git commit 실패
        exit /b 1
    )
)

:: 4. 원격 최신화 (작업트리 깨끗한 상태에서 rebase)
echo.
echo [4/5] git pull --rebase ...
git pull --rebase
if %errorlevel% neq 0 (
    echo [오류] git pull 실패
    exit /b 1
)

:: 5. Push → GitHub Actions 자동 트리거
echo.
echo [5/5] git push ...
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
