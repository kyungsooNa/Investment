@echo off
setlocal
cd /d "%~dp0"

REM 1. main 브랜치 최신화
echo Updating main branch...
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo Git repository not found.
    pause
    exit /b 1
)

git fetch origin main
if errorlevel 1 (
    echo Failed to fetch origin/main.
    pause
    exit /b 1
)

git checkout main
if errorlevel 1 (
    echo Failed to checkout main. Please commit or stash local changes, then try again.
    pause
    exit /b 1
)

git pull --ff-only origin main
if errorlevel 1 (
    echo Failed to pull latest main branch.
    pause
    exit /b 1
)

REM 2. 아나콘다 설정 스크립트 경로 (본인의 경로로 수정 필수)
set CONDAPATH=C:\Users\Kyungsoo\anaconda3\

REM 3. 아나콘다 환경 활성화 준비
call %CONDAPATH%\Scripts\activate.bat %CONDAPATH%

REM 4. 특정 가상환경 활성화 (base 환경이면 생략 가능)
call conda activate py310

call pip install -r requirements.txt

REM 5. 원하는 작업 수행 (예: 파이썬 실행)
python main.py

pause
