# 테스트 Hang 트러블슈팅 가이드

> **언제 이 문서를 참고하나?**
> - `pytest tests/` 실행 중 일부 TC가 무한 대기 (hang) 하여 전체 스위트가 freeze 될 때
> - xdist worker 가 멈추거나, 테스트가 정상 종료되지 않을 때
> - `pytest-asyncio` + `unittest.mock.patch` 관련 데드락 의심 시
>
> **빠른 진단**: 아래 [진단 체크리스트](#진단-체크리스트) 부터 확인.

---

## 증상
`pytest tests/` 전체 실행 시 일부 TC가 무한 대기 (hang) → xdist worker 가 멈춰 전체 스위트가 freeze.

---

## 원인 패턴 1: `@patch` 데코레이터 + `async def` + pytest fixture 혼용 (pytest-asyncio 1.0.0)

**문제**: `asyncio_mode=auto` 환경에서 `@patch` 데코레이터를 `async def` 테스트 함수에 사용하면서 동시에 pytest fixture 를 인자로 받으면 데드락 발생.

```python
# ❌ 데드락 발생 패턴
@patch("module.SomeClass")
async def test_foo(mock_class, my_fixture):  # fixture + @patch 혼용 → hang
    ...
```

**해결**: `@patch` 데코레이터 대신 `with patch()` 컨텍스트 매니저 사용.

```python
# ✅ 올바른 패턴
async def test_foo(my_fixture):
    with patch("module.SomeClass") as mock_class:
        ...
```

---

## 원인 패턴 2: `ClientWithRetryQueue` 를 통한 mock 호출 시 무한 재시도

**문제**: `BrokerAPIWrapper` 는 내부적으로 `ClientWithCache → ClientWithRetryQueue → 실제 클라이언트` 체인으로 구성됨.
테스트에서 mock 이 `ResCommonResponse` 가 아닌 **plain dict / None** 을 반환하면 `classify()` 가 `RETRY` 판정 → `MAX_RETRIES(5)` 회 재시도 → `asyncio.sleep` 지연 누적 → hang.

```python
# ❌ plain dict 반환 → classify() → RETRY → 5회 재시도 → hang
mock_client.some_method.return_value = {"key": "value"}
wrapper = BrokerAPIWrapper(...)  # ClientWithRetryQueue 래핑됨
await wrapper.some_method(...)   # hang!
```

**해결 방법 A (권장)**: `BrokerAPIWrapper` 의 래핑 레이어를 bypass 하여 mock client 를 직접 주입.

```python
# ✅ cache_wrap_client, retry_queue_wrap_client 를 identity 함수로 패치
async def test_delegation(mock_env, mock_logger):
    with patch(f"{wrapper_module.__name__}.KoreaInvestApiClient") as mock_client_class, \
         patch(f"{wrapper_module.__name__}.cache_wrap_client", side_effect=lambda c, *a, **kw: c), \
         patch(f"{wrapper_module.__name__}.retry_queue_wrap_client", side_effect=lambda c, *a, **kw: c):
        wrapper = BrokerAPIWrapper("korea_investment", env=mock_env, logger=mock_logger)
        # wrapper._client 가 mock_client_class.return_value 로 직접 할당됨
```

**해결 방법 B**: 테스트 픽스처가 이미 `BrokerAPIWrapper` 를 생성한 경우 `_client` 를 직접 교체.

```python
# ✅ wrapper 생성 후 _client 를 mock 으로 직접 대체
wrapper = BrokerAPIWrapper(...)
wrapper._client = mock_client_instance  # 래핑 레이어 우회
```

**해결 방법 C**: mock 이 `ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, ...)` 를 반환하도록 변경 → `classify()` → `DONE` → 즉시 완료.

---

## 원인 패턴 3: conftest `fast_sleep` 가 `ApiRequestQueue` 의 sleep 을 커버 못 할 경우

`conftest.py` 의 `fast_sleep` fixture 는 `asyncio.sleep` 을 전역 patch 하지만,
**integration test** conftest 에서는 `core.retry_queue.api_request_queue.asyncio.sleep` 을 별도 patch 해야 할 수 있음.

```python
# integration test conftest 또는 개별 TC 내
@pytest.fixture
def mock_sleep():
    with patch("core.retry_queue.api_request_queue.asyncio.sleep", new_callable=AsyncMock) as m:
        yield m
```

---

## 원인 패턴 4: 픽스처에서 외부 네트워크 호출 누락 패치 → xdist 병렬 실행 시 429

**증상**: `pytest tests/unit_test -n auto` 병렬 실행 시에만 `ERROR ... urllib.error.HTTPError: HTTP Error 429: Too Many Requests` 발생. 단독(`-n0`) 또는 파일 단위 실행에서는 통과.

**원인**: `WebAppContext.__init__` 에서 `StockCodeRepository` 를 직접 인스턴스화하는데, 픽스처가 이를 패치하지 않으면 DB 파일 부재 시 실제 네트워크 요청 발생.

```
WebAppContext.__init__
 └─ StockCodeRepository.__init__
     └─ (DB 파일 없으면) save_stock_code_list(force_update=True)  ← stock_sync_service.py
         ├─ FinanceDataReader (내부적으로 urllib.request 사용)
         └─ pykrx (requests 사용)
```

xdist 가 worker 별 임시 환경을 만들 때 DB 파일이 없는 경우 여러 worker 가 동시에 외부 서버 호출 → 레이트 리밋(429) 발생.

**해결**: `WebAppContext` 를 생성하는 픽스처에서 `StockCodeRepository` 를 반드시 패치.

```python
# ❌ StockCodeRepository 미패치 → xdist 병렬 시 429
with patch('view.web.web_app_initializer.StockRepository') as MockSR, \
     patch('view.web.web_app_initializer.Logger') as MockLogger:
    ctx = WebAppContext(app_context)

# ✅ StockCodeRepository 추가 패치
with patch('view.web.web_app_initializer.StockRepository') as MockSR, \
     patch('view.web.web_app_initializer.StockCodeRepository') as MockSCR, \
     patch('view.web.web_app_initializer.Logger') as MockLogger:
    ctx = WebAppContext(app_context)
```

**진단 포인트**: `ERROR` (FAILED 아님) 이면서 `-n0` 단독 실행에서 통과 → 픽스처 setup 중 실제 네트워크/파일 I/O 호출 누락 패치 의심.

---

## 원인 패턴 5: `start()` 가 생성한 백그라운드 asyncio.Task 미취소 → `asyncio.sleep(long)` hang

**문제**: `task.start()` 내부에서 `asyncio.create_task(start_after_market_scheduler())` 를 생성한다.
이 Task 는 `run_after_market_loop()` 의 `while True:` 루프를 실행한다.
테스트에서 `mcs=None`, `market_clock=None` 으로 생성하면 루프 마지막의 `_smart_sleep()` → `asyncio.sleep(12 * 3600)` (12시간 대기) 에 진입한다.
테스트 함수가 정상 종료해도 이 백그라운드 Task 가 살아 있어 pytest-asyncio 가 이벤트 루프를 닫지 못하고 hang.

```python
# ❌ 백그라운드 Task 미취소 → 12시간 sleep 으로 hang
async def test_lifecycle():
    task = MinerviniUpdateTask(minervini_service=DummyMinerviniSvc({}))
    await task.start()           # asyncio.create_task(무한루프) 생성
    await task.suspend()
    await task.resume()
    # 테스트 종료 — 백그라운드 Task 는 여전히 sleep(43200) 중 → hang
```

**해결**: 테스트 종료 전 반드시 `await task.stop()` 으로 백그라운드 Task 를 취소한다.
`try/finally` 를 사용해 assertion 실패 시에도 정리되도록 보장.

```python
# ✅ finally 블록에서 stop() 호출
async def test_lifecycle():
    task = MinerviniUpdateTask(minervini_service=DummyMinerviniSvc({}))
    try:
        await task.start()
        assert task.state == TaskState.RUNNING
        await task.suspend()
        assert task.state == TaskState.SUSPENDED
        await task.resume()
        assert task.state == TaskState.RUNNING
    finally:
        await task.stop()  # 백그라운드 스케줄러 Task 취소
```

**적용 범위**: `start()` 가 `asyncio.create_task()` 로 백그라운드 루프를 생성하는 모든 태스크 클래스
(`AfterMarketTask` 서브클래스 전체 — `MinerviniUpdateTask`, `DailyPriceCollectorTask`, `RankingTask` 등).

---

## 진단 체크리스트

TC 가 hang 할 때 아래 순서로 확인:

1. **`-n0` 으로 단독 실행** → 여전히 hang 하면 xdist 문제 아님, TC 자체 문제
   ```bash
   pytest tests/unit_test/test_foo.py::test_bar -v -n0
   ```
2. **`@patch` 데코레이터 + `async def` + fixture 혼용** 여부 확인 → `with patch()` 로 교체 (→ [원인 패턴 1](#원인-패턴-1-patch-데코레이터--async-def--pytest-fixture-혼용-pytest-asyncio-100))
3. **mock 반환값이 `ResCommonResponse` 인지** 확인 → plain dict/None 이면 RETRY 루프 진입 가능 (→ [원인 패턴 2](#원인-패턴-2-clientwithretryqueue-를-통한-mock-호출-시-무한-재시도))
4. **`BrokerAPIWrapper` 를 직접 생성하는 TC** 인지 확인 → `cache_wrap_client` / `retry_queue_wrap_client` bypass 패치 적용 (→ [원인 패턴 2](#원인-패턴-2-clientwithretryqueue-를-통한-mock-호출-시-무한-재시도))
5. **`asyncio.sleep` 이 제대로 mock** 되는지 확인 → `fast_sleep` autouse fixture 가 동작 범위 내인지 점검 (→ [원인 패턴 3](#원인-패턴-3-conftest-fast_sleep-가-apirequestqueue-의-sleep-을-커버-못-할-경우))
6. **ERROR(FAILED 아님) + `-n0` 단독 통과** → 픽스처 setup 중 외부 I/O 누락 패치 의심 → `WebAppContext` 생성 픽스처에서 `StockCodeRepository` 등 네트워크 호출 가능 클래스 패치 확인 (→ [원인 패턴 4](#원인-패턴-4-픽스처에서-외부-네트워크-호출-누락-패치--xdist-병렬-실행-시-429))
7. **`task.start()` 를 호출하는 TC** → `asyncio.create_task(무한루프)` 생성 여부 확인 → 테스트 종료 전 `await task.stop()` 호출 (`try/finally` 블록 권장) (→ [원인 패턴 5](#원인-패턴-5-start-가-생성한-백그라운드-asynciotask-미취소--asynciosleeplong-hang))
