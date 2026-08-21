# Kiwoom API Integration Plan

## 목표

키움증권 API를 기존 한국투자증권 중심 구조에 두 번째 브로커로 추가한다.
1차 목표는 기존 서비스 계층을 크게 바꾸지 않고 키움 REST API 기반으로 시세 조회, 계좌 조회, 주문, 토큰 관리를 연결하는 것이다.
2차 목표는 키움 WebSocket과 조건검색/특화 데이터 기능을 붙여 한국투자 API의 빈 영역을 보완하는 것이다.

## 핵심 가정

- 기본 연동 방식은 키움 REST API를 우선한다.
- 기존 OCX 기반 OpenAPI+는 Windows/ActiveX 제약이 크므로 1차 구현 범위에서 제외한다.
- 한국투자 API와 키움 API의 원본 응답은 다르지만, 서비스 계층에는 기존 `ResCommonResponse` 및 공통 도메인 타입으로 변환해서 전달한다.
- 키움에서만 제공되거나 키움 쪽이 더 강한 기능은 공통 브로커 인터페이스에 억지로 끼워 넣지 않고, `capabilities` 또는 브로커별 확장 API로 분리한다.

## 기대 이점

- 한국투자 API 장애, 점검, 호출 제한 발생 시 브로커 우회 경로를 확보한다.
- 한국투자에 없거나 제한적인 조건검색, 실시간 이벤트, 특화 랭킹/종목 발굴 기능을 보완할 수 있다.
- 동일 전략을 한국투자/키움 양쪽에서 비교해 체결 품질, 데이터 지연, 응답 안정성을 검증할 수 있다.
- 전략별로 브로커를 분리해 계좌와 리스크를 나누는 운영이 가능해진다.
- 이후 다른 증권사 API를 붙일 때도 멀티 브로커 구조를 재사용할 수 있다.

## 1차 범위

### 포함

- 키움 설정 로딩
- 키움 OAuth 토큰 발급, 저장, 재사용, 만료 갱신
- 공통 HTTP API base 구현
- 현재가/종목 기본 정보 조회
- 일봉/분봉 조회
- 계좌 잔고/예수금 조회
- 매수/매도 주문
- 주문 가능 수량 조회
- `BrokerAPIWrapper`에서 `kiwoom` 선택 지원
- 단위 테스트 및 통합 테스트

### 제외

- OCX OpenAPI+ 직접 연동
- 실제 주문 자동 실행
- 모든 키움 API 전체 래핑
- Web UI 대규모 개편
- 기존 한국투자 API 리팩토링

## 2차 범위

- 키움 WebSocket 실시간 체결가 구독
- 호가 구독
- 실시간 조건검색 편입/이탈 이벤트
- 키움 특화 랭킹/종목 발굴 API
- 한국투자/키움 데이터 교차 검증
- 브로커별 기능 지원표 UI 노출

## 디렉터리 구조

```text
brokers/kiwoom/
├── __init__.py
├── kiwoom_env.py
├── kiwoom_token_provider.py
├── kiwoom_api_base.py
├── kiwoom_client.py
├── kiwoom_quotations_api.py
├── kiwoom_account_api.py
├── kiwoom_trading_api.py
├── kiwoom_websocket_api.py
├── kiwoom_header_provider.py
├── kiwoom_url_provider.py
├── kiwoom_params_provider.py
└── kiwoom_capabilities.py
```

테스트는 아래 위치에 추가한다.

```text
tests/unit_test/brokers/kiwoom/
├── test_kiwoom_env.py
├── test_kiwoom_token_provider.py
├── test_kiwoom_api_base.py
├── test_kiwoom_quotations_api.py
├── test_kiwoom_account_api.py
├── test_kiwoom_trading_api.py
└── test_kiwoom_capabilities.py

tests/integration_test/brokers/
└── test_it_kiwoom_broker_api_wrapper.py
```

## 설정 계획

`config/config.yaml.example`에 키움 설정 예시를 추가한다.

```yaml
kiwoom:
  is_paper_trading: true
  paper:
    app_key: ""
    app_secret: ""
    account_no: ""
    base_url: ""
    websocket_url: ""
  real:
    app_key: ""
    app_secret: ""
    account_no: ""
    base_url: ""
    websocket_url: ""
  token:
    path: "config/token_kiwoom.json"
    refresh_margin_seconds: 300
```

주의점:

- 실제 키와 계좌번호는 `config/config.yaml`에만 둔다.
- `config/config.yaml`과 `token_kiwoom*.json`은 git에 포함하지 않는다.
- 한국투자 설정 구조와 최대한 비슷하게 맞춰 사용자가 브로커를 바꿀 때 혼란을 줄인다.

## 공통 인터페이스 계획

기존 서비스가 기대하는 기능을 먼저 목록화한다.

```text
시세:
- get_stock_full_info
- get_daily_price
- get_minute_price
- get_stock_price

계좌:
- get_balance
- get_deposit
- get_possible_order_qty

주문:
- buy
- sell
- cancel_order
- modify_order
```

키움에서 지원하지 않거나 응답 의미가 다른 기능은 다음 방식 중 하나로 처리한다.

- 공통 기능이면 키움 내부에서 응답을 표준 타입으로 변환한다.
- 브로커별 특화 기능이면 `kiwoom_client.specialized` 또는 별도 service를 둔다.
- 지원 불가 기능이면 `ErrorCode.UNSUPPORTED_FEATURE`로 명확히 반환한다.

## 기능 지원표

| 기능 | 한국투자 | 키움 | 구현 우선순위 |
| --- | --- | --- | --- |
| 현재가 조회 | 지원 | 지원 | P0 |
| 일봉 조회 | 지원 | 지원 | P0 |
| 분봉 조회 | 지원 | 지원 | P0 |
| 계좌 잔고 | 지원 | 지원 | P0 |
| 예수금 | 지원 | 지원 | P0 |
| 매수/매도 주문 | 지원 | 지원 | P0 |
| 주문 정정/취소 | 지원 | 지원 | P1 |
| 실시간 체결가 | 지원 | 지원 | P1 |
| 실시간 호가 | 지원 | 지원 | P1 |
| 조건검색 | 제한/미지원 가능 | 강점 영역 | P2 |
| 특화 랭킹/종목 발굴 | 일부 지원 | 보완 후보 | P2 |
| 데이터 교차 검증 | 해당 없음 | 조합 기능 | P2 |

## 구현 단계

### Phase 0: API 명세 확인

검증 기준:

- 키움 REST API의 인증 방식, 토큰 응답, 주문 API 필수 필드, 시세 API 응답 필드를 확인한다.
- 현재 프로젝트의 한국투자 클라이언트 공개 메서드와 서비스 호출 지점을 정리한다.
- 1차 구현 API 목록을 최종 확정한다.

작업:

- 공식 문서 기준으로 endpoint, method, header, request body, response body를 표로 정리한다.
- 한국투자 API와 1:1 매핑 가능한 기능과 불가능한 기능을 분리한다.
- 실제 계좌 인증이 필요한 스모크 테스트는 자동 테스트와 분리한다.

### Phase 1: 설정과 환경 객체

검증 기준:

- `KiwoomEnv`가 모의/실전 설정을 올바르게 선택한다.
- 필수 설정 누락 시 명확한 예외 또는 실패 응답을 반환한다.

테스트:

```powershell
pytest tests/unit_test/brokers/kiwoom/test_kiwoom_env.py -v
```

구현:

- `brokers/kiwoom/kiwoom_env.py`
- `config/config.yaml.example` 키움 섹션
- token path, base url, app key, app secret, account no 접근자

### Phase 2: 토큰 제공자

검증 기준:

- 토큰 파일이 없으면 새 토큰을 발급한다.
- 토큰이 유효하면 재사용한다.
- 만료 임박 토큰은 갱신한다.
- 발급 실패 응답을 표준 오류로 변환한다.

테스트:

```powershell
pytest tests/unit_test/brokers/kiwoom/test_kiwoom_token_provider.py -v
```

구현:

- `brokers/kiwoom/kiwoom_token_provider.py`
- 토큰 저장/로드
- 만료 시간 계산
- 토큰 발급 HTTP 호출 mock 테스트

### Phase 3: HTTP base

검증 기준:

- 모든 요청에 인증 헤더가 들어간다.
- HTTP 오류와 키움 오류 응답이 `ResCommonResponse`로 변환된다.
- 타임아웃과 네트워크 오류가 재시도 레이어에서 분류 가능하게 유지된다.

테스트:

```powershell
pytest tests/unit_test/brokers/kiwoom/test_kiwoom_api_base.py -v
```

구현:

- `brokers/kiwoom/kiwoom_api_base.py`
- 공통 `get`, `post`
- response normalization
- logger 연동

### Phase 4: 시세 API

검증 기준:

- 현재가, 일봉, 분봉 응답을 기존 서비스가 소비 가능한 구조로 변환한다.
- 키움 필드 누락/빈 응답 케이스를 안전하게 처리한다.

테스트:

```powershell
pytest tests/unit_test/brokers/kiwoom/test_kiwoom_quotations_api.py -v
```

구현:

- `brokers/kiwoom/kiwoom_quotations_api.py`
- `kiwoom_url_provider.py`
- `kiwoom_params_provider.py`
- 필요한 경우 `common/types.py`에 공통 타입 최소 추가

### Phase 5: 계좌 API

검증 기준:

- 보유 종목, 평가금액, 예수금, 주문 가능 수량을 표준 응답으로 변환한다.
- 계좌번호/상품코드 등 키움 전용 파라미터를 `KiwoomEnv`에서 일관되게 공급한다.

테스트:

```powershell
pytest tests/unit_test/brokers/kiwoom/test_kiwoom_account_api.py -v
```

구현:

- `brokers/kiwoom/kiwoom_account_api.py`
- balance/deposit/possible quantity response mapper

### Phase 6: 주문 API

검증 기준:

- 매수/매도 주문 요청이 키움 필수 필드로 변환된다.
- 주문 성공/실패 응답이 공통 타입으로 변환된다.
- 테스트에서는 실제 주문을 절대 호출하지 않는다.

테스트:

```powershell
pytest tests/unit_test/brokers/kiwoom/test_kiwoom_trading_api.py -v
```

구현:

- `brokers/kiwoom/kiwoom_trading_api.py`
- 시장가/지정가 매수
- 시장가/지정가 매도
- 주문 정정/취소는 P1로 미룰 수 있음

### Phase 7: 통합 클라이언트와 wrapper 연결

검증 기준:

- `BrokerAPIWrapper("kiwoom", ...)`가 키움 클라이언트를 생성한다.
- 기존 cache/retry wrapper가 키움 클라이언트에도 적용된다.
- 서비스 계층 호출 테스트에서 키움 mock client가 정상 연결된다.

테스트:

```powershell
pytest tests/unit_test/brokers/test_broker_api_wrapper.py -v
pytest tests/integration_test/brokers/test_it_kiwoom_broker_api_wrapper.py -v
```

구현:

- `brokers/kiwoom/kiwoom_client.py`
- 기존 `BrokerAPIWrapper`의 브로커 선택 분기 확장
- 필요한 경우 broker factory 도입

### Phase 8: WebSocket

검증 기준:

- 키움 실시간 연결, 구독, 수신, 해제가 가능하다.
- 연결 끊김 시 재연결 정책이 기존 한국투자 WebSocket과 충돌하지 않는다.
- 테스트에서 receive task가 남아 pytest를 hang시키지 않는다.

테스트:

```powershell
pytest tests/unit_test/brokers/kiwoom/test_kiwoom_websocket_api.py -v
```

구현:

- `brokers/kiwoom/kiwoom_websocket_api.py`
- 체결가 구독
- 호가 구독
- subscribe/unsubscribe
- stop/cleanup 보장

### Phase 9: 키움 특화 기능

검증 기준:

- 한국투자에 없는 기능을 키움 특화 기능으로 노출한다.
- 공통 서비스 계층을 오염시키지 않는다.

후보:

- 조건검색 결과 조회 또는 실시간 조건검색 이벤트
- 키움 특화 랭킹
- 장중 급등/거래량/수급 후보군
- 한국투자/키움 가격 교차 검증

구현 방향:

- `services/broker_capability_service.py`
- `brokers/kiwoom/kiwoom_capabilities.py`
- Web에서는 브로커별 지원 기능을 숨기거나 비활성화한다.

## 테스트 전략

### 단위 테스트

- HTTP 호출은 모두 mock 처리한다.
- 토큰 파일은 임시 디렉터리를 사용한다.
- 실제 계좌번호, 실제 app key, 실제 주문 호출은 사용하지 않는다.

명령:

```powershell
pytest tests/unit_test -v
```

### 통합 테스트

- 키움 API wrapper가 기존 서비스 계층과 연결되는지만 검증한다.
- 외부 네트워크 호출은 mock으로 차단한다.
- WebAppContext를 건드릴 경우 `StockCodeRepository` 외부 호출을 반드시 patch한다.

명령:

```powershell
pytest tests/integration_test -v
```

### 수동 스모크 테스트

실제 API 키가 있는 로컬 환경에서만 실행한다.

```text
scripts/check_kiwoom_connection.py
```

확인 항목:

- 토큰 발급 성공
- 현재가 조회 성공
- 계좌 잔고 조회 성공
- 모의투자 주문 가능 수량 조회 성공
- 모의투자 소액 주문/취소 성공

실전 주문은 별도 승인 전까지 스모크 테스트에 포함하지 않는다.

## 리스크와 대응

| 리스크 | 대응 |
| --- | --- |
| 키움 REST API와 OCX OpenAPI+ 기능 차이 | REST 우선, OCX 필요 기능은 별도 Phase로 분리 |
| 응답 필드 의미 차이 | 키움 모듈 내부 mapper에서 공통 타입으로 변환 |
| 주문 파라미터 실수 | 주문 API 테스트를 가장 보수적으로 작성하고 모의투자 먼저 검증 |
| 토큰 만료/갱신 오류 | 토큰 provider 단위 테스트를 세분화 |
| WebSocket task hang | 모든 start 테스트는 stop cleanup을 `finally`에서 보장 |
| 브로커별 기능 불일치 | `capabilities`로 지원 여부를 명시 |

## 완료 기준

1차 완료:

- `BrokerAPIWrapper("kiwoom")`로 키움 클라이언트 생성 가능
- 키움 토큰 발급/갱신 테스트 통과
- 현재가/일봉/분봉 mock 조회 테스트 통과
- 잔고/예수금 mock 조회 테스트 통과
- 매수/매도 mock 주문 테스트 통과
- 전체 단위 테스트 통과
- 전체 통합 테스트 통과

2차 완료:

- 키움 WebSocket 체결가/호가 구독 테스트 통과
- 조건검색 또는 키움 특화 후보군 기능 1개 이상 연결
- 한국투자/키움 데이터 교차 검증 기능 추가

## 권장 작업 순서

1. `KiwoomEnv` 테스트 작성
2. `KiwoomEnv` 구현
3. `KiwoomTokenProvider` 테스트 작성
4. `KiwoomTokenProvider` 구현
5. `KiwoomApiBase` 테스트 작성
6. `KiwoomApiBase` 구현
7. 시세 API 테스트와 구현
8. 계좌 API 테스트와 구현
9. 주문 API 테스트와 구현
10. `KiwoomClient`와 `BrokerAPIWrapper` 연결
11. 단위 테스트 전체 실행
12. 통합 테스트 전체 실행
13. 모의투자 수동 스모크 테스트 스크립트 추가
14. WebSocket 및 특화 기능으로 확장
