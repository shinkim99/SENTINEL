@echo off
:: SENTINEL - KR regulatory data collection + git push
:: Run from repo root or via Task Scheduler.
:: Requirements: git, system python, LAW_GO_KR_API_KEY in .env

setlocal

:: Change to repo root (parent of scripts\)
cd /d "%~dp0.."

echo.
echo ============================================================
echo  SENTINEL KR collect  %DATE% %TIME%
echo ============================================================

:: [1/5] Collect KR data -> data/inbox/kr_latest.json
echo.
echo [1/5] python -m scripts.collect_kr ...
python -m scripts.collect_kr
if %errorlevel% neq 0 (
    echo [ERROR] KR collection failed (exitcode=%errorlevel%) -- git push skipped
    exit /b 1
)

:: [2/5] Stage result (must be clean before pull --rebase)
echo.
echo [2/5] git add data\inbox\kr_latest.json ...
git add data\inbox\kr_latest.json

:: [3/5] Commit if changed, skip if identical
echo.
echo [3/5] git commit (skip if no change) ...
git diff --staged --quiet
if %errorlevel% equ 0 (
    echo [INFO] kr_latest.json unchanged -- commit skipped
) else (
    for /f %%i in ('python -c "import datetime; d=datetime.date.today().isocalendar(); print(str(d[0])+'-W'+str(d[1]).zfill(2))"') do set WEEK=%%i
    echo commit: chore: KR collection %WEEK%
    git commit -m "chore: KR collection %WEEK%"
    if %errorlevel% neq 0 (
        echo [ERROR] git commit failed
        exit /b 1
    )
)

:: [4/5] Pull latest remote (worktree is clean -> rebase succeeds)
echo.
echo [4/5] git pull --rebase ...
git pull --rebase
if %errorlevel% neq 0 (
    echo [ERROR] git pull failed
    exit /b 1
)

:: [5/5] Push -> triggers GitHub Actions digest workflow
echo.
echo [5/5] git push ...
git push
if %errorlevel% neq 0 (
    echo [ERROR] git push failed
    exit /b 1
)

echo.
echo ============================================================
echo  Done. GitHub Actions will run the digest automatically.
echo  Status: https://github.com/shinkim99/SENTINEL/actions
echo ============================================================
exit /b 0
