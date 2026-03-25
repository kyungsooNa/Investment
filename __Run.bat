@echo off
REM 1. 아나콘다 설정 스크립트 경로 (본인의 경로로 수정 필수)
set CONDAPATH=C:\Users\Kyungsoo\anaconda3\

REM 2. 아나콘다 환경 활성화 준비
call %CONDAPATH%\Scripts\activate.bat %CONDAPATH%

REM 3. 특정 가상환경 활성화 (base 환경이면 생략 가능)
call conda activate py310

call pip install -r requirements.txt

REM 4. 원하는 작업 수행 (예: 파이썬 실행)
python main.py

pause