@echo off
:: SENTINEL - KR regulatory data collection + git push
:: Requirements: git, system python, LAW_GO_KR_API_KEY in .env
setlocal
cd /d "%~dp0.."

echo SENTINEL KR collect - %DATE% %TIME%

echo.
echo [1/5] python -m scripts.collect_kr
python -m scripts.collect_kr
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] step 1 collect failed
    exit /b 1
)

echo.
echo [2/5] git add
git add data\inbox\kr_latest.json
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] step 2 git add failed
    exit /b 1
)

echo.
echo [3/5] git commit
git diff --staged --quiet
if %ERRORLEVEL% NEQ 0 goto :do_commit
echo [INFO] no changes - commit skipped
goto :after_commit

:do_commit
python -c "import datetime; g=datetime.date.today().isocalendar(); print(str(g[0])+'-W'+str(g[1]).zfill(2))" > .week.tmp
set /p WEEK= < .week.tmp
del .week.tmp 2>nul
git commit -m "chore: KR collection %WEEK%"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] step 3 commit failed
    exit /b 1
)
:after_commit

echo.
echo [4/5] git pull --rebase
git pull --rebase
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] step 4 pull failed
    exit /b 1
)

echo.
echo [5/5] git push
git push
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] step 5 push failed
    exit /b 1
)

echo.
echo Done - GitHub Actions will start automatically.
echo https://github.com/shinkim99/SENTINEL/actions
exit /b 0
