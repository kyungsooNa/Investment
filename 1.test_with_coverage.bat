@echo off

REM 가상환경 활성화
REM 가상환경 경로가 올바른지 확인하세요.
call C:\Users\Kyungsoo\anaconda3\Scripts\activate.bat py310

REM 테스트 실행 + 병렬 실행 + 커버리지 리포트 출력 (HTML + 터미널 컬러)
REM -n auto 옵션을 추가하여 병렬 실행을 활성화합니다.
REM --cov-config=.coveragerc 옵션은 .coveragerc 파일에 설정이 있을 경우 사용합니다.
pytest --color=yes ^
       -n auto ^
       --cov=brokers ^
       --cov=api ^
       --cov=services ^
       --cov=core ^
       --cov-report=term-missing ^
       --cov-report=html ^
       --cov-config=.coveragerc

REM HTML 커버리지 리포트 자동 열기
start htmlcov\index.html

REM 테스트 완료 메시지 (선택 사항)
echo.
echo All tests finished.