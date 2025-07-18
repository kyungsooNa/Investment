@echo off

REM 가상환경 활성화
call C:\Users\Kyungsoo\anaconda3\Scripts\activate.bat py310

REM 테스트 실행 + 병렬 실행 + 커버리지 리포트 출력 (HTML + 터미널 컬러)
pytest --color=yes ^
    -n auto ^
    --cov=. ^
    --cov-report=term-missing ^
    --cov-report=html ^
    --cov-config=.coveragerc

REM HTML 커버리지 리포트 자동 열기
start htmlcov\index.html

REM 테스트 완료 메시지
echo.
echo All tests finished.
pause
