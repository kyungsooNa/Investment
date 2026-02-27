## Pytest 실행 방법 예시
cd /c/Users/Kyungsoo/Documents/Code/Investment && /c/Users/Kyungsoo/anaconda3/envs/py310/python.exe -m pytest tests/unit_test/test_korea_invest_websocket_api.py::test_disconnect_with_receive_task_exception -v 2>&1 | tail -50


## TC 수행 시간은 1.5초 이내여야함. 1.5초 초과시 단축할 수 있는지 확인.