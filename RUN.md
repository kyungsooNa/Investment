# 실행 및 테스트 가이드

## 환경 설정 (처음 한 번)

프로젝트는 **httpx, requests, pandas** 등 외부 패키지를 사용합니다. Anaconda의 **py310** 환경에서 아래처럼 설치하면 됩니다.

1. **터미널에서 (PowerShell 또는 Anaconda Prompt)**  
   ```powershell
   conda activate py310
   cd c:\Users\Kyungsoo\Documents\Code\Investment
   pip install -r requirements.txt
   ```
2. **Cursor에서 사용할 인터프리터 지정**  
   - `Ctrl+Shift+P` → **Python: Select Interpreter**  
   - 목록에서 **py310** (또는 `C:\Users\Kyungsoo\anaconda3\envs\py310\python.exe`) 선택  

이렇게 하면 IDE에서 **Run** 버튼으로 실행할 때도 같은 환경이 사용되어 `httpx` 등을 찾을 수 있습니다.

**인터프리터를 py310으로 바꿨는데도 `httpx` 에러가 나는 경우**  
- Cursor가 실제로는 **다른 Python**(예: python.org 설치본)을 쓰고 있을 수 있습니다.  
- **해결 1**: **F5**(Run and Debug)로 실행해 보세요. 왼쪽 **Run and Debug** → **"Run main.py (py310)"** 선택 후 ▶ 실행.  
  (`.vscode/launch.json`에 py310 경로를 지정해 두었습니다.)  
- **해결 2**: Cursor **터미널**(`` Ctrl+` ``)에서  
  `python -c "import sys; print(sys.executable)"`  
  로 **실제 사용 중인 Python 경로**를 확인한 뒤,  
  `"해당경로" -m pip install -r requirements.txt`  
  로 그 환경에 패키지를 설치하세요.

---

## IDE UI에서 실행 (Cursor 화면에서)

### 앱(main.py) 실행
1. **F5 (Run and Debug)** — 권장  
   - **F5** 키를 누르거나, 왼쪽 **Run and Debug** 패널에서 **"Run main.py (py310)"** 선택 후 ▶ 실행.  
   - **왜 Run 버튼은 오류가 나나요?**  
     IDE의 **▶ Run Python File** 버튼은 "선택된 인터프리터"를 쓰는데, Cursor에서 이게 py310이 아닌 다른 Python으로 잡혀 있으면 `httpx` 등이 없어 오류가 납니다.  
     **F5**는 `launch.json`에 지정한 **Anaconda py310** 경로를 그대로 사용해서 정상 동작합니다.  
   - **정리**: 이 프로젝트에서는 **Run 버튼 대신 F5**로 실행하는 것을 권장합니다.

2. **터미널에서**: `` Ctrl+` `` 로 터미널 열고 `conda activate py310` 후 `python main.py`

3. 종료: 터미널에서 **Ctrl+C** 또는 창 닫기.

### Test Explorer 사용 방법 (테스트 뷰)

Test Explorer는 PyCharm의 테스트 창처럼 **테스트 목록을 보고 골라서 실행**할 수 있는 패널입니다.

#### 1단계: Test Explorer 열기
- 왼쪽 **사이드바**에서 **플라스크(🧪) 아이콘** 클릭  
  (이름: **Testing** 또는 **Test Explorer**)
- 또는 **Ctrl+Shift+P** → **"Testing: Focus on Test View"** 입력 후 실행

#### 2단계: Python 인터프리터 선택 (처음 한 번)
- **Ctrl+Shift+P** → **"Python: Select Interpreter"** 실행
- 목록에서 **py310** 또는 **`...\anaconda3\envs\py310\python.exe`** 선택  
  → 테스트가 이 환경에서 실행됩니다.

#### 3단계: 테스트 불러오기 (Discover)
- Test Explorer 상단의 **"Discover Tests"** 버튼 클릭  
  또는 **새로고침(🔄)** 아이콘 클릭
- 잠시 후 `tests/unit_test`, `tests/integration_test` 아래에 테스트 파일·케이스가 트리로 나열됩니다.

#### 4단계: 테스트 실행
| 하고 싶은 것 | 동작 |
|-------------|------|
| **전체 실행** | 상단 **Run All** (▶▶ 아이콘) 클릭 |
| **한 파일만** | `tests/unit_test` 등 폴더를 펼친 뒤, 원하는 파일(예: `test_trading_service.py`) 옆 **▶** 클릭 |
| **한 케이스만** | 테스트 함수(예: `test_buy_order_success`) 옆 **▶** 클릭 |

#### 5단계: 결과 보기
- 실행이 끝나면 각 테스트 옆에 **✓(통과)** 또는 **✗(실패)** 표시
- 실패한 테스트를 클릭하면 **오류 메시지**와 **스택**이 아래나 패널에 표시됩니다.
- 터미널 패널에서 pytest 출력도 확인할 수 있습니다.

#### 테스트가 안 보이거나 오류가 날 때
- **Python 확장**이 설치되어 있는지 확인 (Ctrl+Shift+X → "Python" 검색)
- **인터프리터**가 py310인지 다시 확인 (우측 하단 상태 표시줄에 `Python 3.10.x ('py310')` 등 표시)
- **Discover Tests**를 다시 한 번 실행
- 터미널에서 `pytest tests --collect-only` 로 테스트가 수집되는지 확인

### 디버그 실행 (브레이크포인트 사용)
1. `main.py` 또는 테스트 파일에서 **줄 번호 왼쪽**을 클릭해 빨간 점(브레이크포인트)을 찍습니다.
2. **F5** 키를 누르거나 왼쪽 **Run and Debug(▶▷)** 아이콘 → **Run and Debug** 클릭.
3. 처음이면 **Python File** 또는 **Python Debugger** 로 실행 구성을 선택합니다.

---

## 1. 앱 실행 (main.py)

### 조건
- **config/config.yaml** 이 있어야 합니다. (민감 정보라 Git에는 없음)
- 없으면: `config/config.yaml.example` 을 복사해 `config/config.yaml` 로 저장한 뒤, 한국투자증권 API 키·계좌번호 등을 채우세요.

### 방법

**방법 A – 터미널에서**
```powershell
cd c:\Users\Kyungsoo\Documents\Code\Investment
# 가상환경 사용 시 (예: Anaconda py310)
conda activate py310
# 실행
python main.py
```

**방법 B – Cursor/VS Code**
1. `main.py` 열기
2. 우측 상단 **Run Python File** (▶) 버튼 클릭  
   또는 F5로 디버그 실행 (Run and Debug 사용 시)

**방법 C – 기존 배치 파일**
- 앱 실행용 배치가 있다면 그대로 사용해도 됩니다. (현재 repo에는 **테스트용** `1.test_with_coverage.bat`, `2.integration_test_with_coverage.bat` 만 있음)

---

## 2. 테스트 실행 (Test Explorer)

**자세한 단계는 위의 "Test Explorer 사용 방법"** 절을 참고하세요. 요약만 적으면:

1. **Python 확장** 설치 여부 확인 (Ctrl+Shift+X → "Python" 검색).
2. **Test Explorer 열기**: 사이드바 **🧪 Testing** 아이콘 또는 **Ctrl+Shift+P** → "Testing: Focus on Test View".
3. **인터프리터**: **Python: Select Interpreter** 로 **py310** 선택.
4. **Discover Tests**로 테스트 목록 불러오기.
5. **Run All** / 파일 옆 ▶ / 케이스 옆 ▶ 로 실행.

### 터미널에서 pytest 직접 실행

```powershell
cd c:\Users\Kyungsoo\Documents\Code\Investment
conda activate py310

# 단위 테스트만
pytest tests/unit_test -v

# 단위 + 통합 (병렬, pytest.ini 설정 따름)
pytest tests -v

# 커버리지까지 (기존 배치와 동일)
.\1.test_with_coverage.bat
# 또는
pytest --cov=. --cov-report=term-missing --cov-report=html --cov-config=.coveragerc
```

### 설정 요약 (.vscode/settings.json)

- **pytest** 사용으로 설정해 두었습니다. (테스트 뷰가 pytest 기준으로 동작)
- 테스트 경로: `tests` (단위/통합 모두 포함)
- `pytest.ini` 의 `-n auto`, `--durations` 등은 그대로 적용됩니다.

---

## 3. 요약

| 하려는 일       | IDE UI에서 | 터미널에서 |
|----------------|------------|------------|
| **앱 실행**    | **F5** (Run and Debug → "Run main.py (py310)") | `conda activate py310` 후 `python main.py` |
| **테스트 전체** | **Test Explorer** → Discover Tests → **Run All** | `pytest tests -v` |
| **테스트 일부** | Test Explorer에서 파일/케이스 옆 **▶** | `pytest tests/unit_test/... -v` |
| **디버그**     | 브레이크포인트 찍고 **F5** | - |

PyCharm에서 쓰던 **Conda py310** 을 Cursor에서도 인터프리터로 선택하면, 같은 환경으로 실행·테스트할 수 있습니다.
