# 외부 공개 배포 보완 계획

## 목적

현재 웹 앱은 FastAPI 기반 운영 도구이며 주문 기능, 증권사 API 키, 계좌 정보, 실시간 구독, 백그라운드 자동매매 태스크를 포함한다. 외부 공개 전에는 배포 환경 구성보다 서버 측 접근 통제와 실전 주문 차단을 먼저 완성해야 한다.

## 코드 대조로 확인한 현재 위험

- HTML 페이지는 `web_main.py`의 `render_page()`가 `use_login=true`일 때 로그인 쿠키를 검사한다.
- JSON API 계층에는 공통 인증이 없다. `check_auth()`를 직접 호출하는 라우트는 현재 `routes/kill_switch.py` 한 파일의 4개 엔드포인트뿐이다.
- 그 `check_auth()`도 로그인과 다른 설정 경로를 사용한다. 로그인은 `ctx.full_config.auth`를 읽지만 `check_auth()`는 `ctx.env.active_config.auth`를 읽고, `KoreaInvestApiEnv.get_full_config()`가 만드는 `active_config`에는 `auth`가 포함되지 않는다. 초기화 상태에 따라 오류가 발생하거나 쿠키와 기대값이 모두 `None`이 되어 인증이 통과할 수 있다.
- 따라서 `/api/order`, `/api/overseas/order`, `/api/balance`, `/api/system/*`, `/api/scheduler/*`, `/api/background/*/force-update` 등을 로그인 페이지를 거치지 않고 직접 호출할 수 있다.
- 국내 실전 주문은 `real_order_confirmation == "REAL"`만 요구하고, 해외 실전 주문은 이 조건과 `overseas_stock.allow_live_trading`을 함께 요구한다.
- 로그인 성공 시 `secret_key` 원문을 만료 없는 정적 쿠키 값으로 사용한다. 사용자, 역할, 토큰 회전 개념이 없으며 로그인 실패 제한도 없다.
- Kill Switch 상태 변경 라우트는 `access_token` 쿠키 원문을 운영자 식별자로 서비스에 전달한다. 현재 쿠키 값이 `secret_key`이므로 감사 로그나 상태 저장소에 인증 비밀이 남을 수 있다.
- `/balance` 페이지는 인증 검사 전에 계좌 조회를 실행한 뒤 그 결과를 `initial_data`로 서버 렌더 컨텍스트에 포함한다. 비인증 응답에 데이터를 렌더하지 않더라도 비로그인 요청이 broker API와 foreground 자원을 소비할 수 있다.
- `operator_dashboard_router`는 통합 API 라우터에 포함된 뒤 `web_main.py`에서 `/api` prefix로 다시 등록된다. 통합 라우터에만 인증 dependency를 적용하면 중복 등록 경로가 보호 정책을 우회할 수 있다.
- SSE는 HTTP 미들웨어의 적용을 받지만 WebSocket 라우트는 HTTP 인증 미들웨어만으로 보호되지 않는다.

현재 최우선 위험은 공개 모드 기능의 부재가 아니라 **대부분의 API가 서버 측 인증 없이 실행되는 상태**다.

## 기본 원칙

- 인증은 화면 표시 여부가 아니라 FastAPI 서버의 API, SSE, WebSocket 경계에서 강제한다.
- 외부 공개 환경에서는 기본적으로 모의투자 또는 데모 데이터만 사용한다.
- 실전 주문은 설정이 누락되거나 충돌하면 허용하지 않는 fail-close 방식으로 처리한다.
- 민감 정보 마스킹은 JSON 응답, 서버 렌더 컨텍스트, 로그를 모두 대상으로 한다.
- 운영 앱과 공개 데모는 배포 환경과 자격 증명을 분리한다.

## 구현 현황

- Phase 0: 구현 및 단위·통합 테스트 완료
- Phase 1: 미구현
- Phase 2: 미구현
- Phase 3: 미구현

Phase 0에서는 API·SSE·WebSocket 공통 인증, 인증 fail-open 제거, 인증 전 `/balance` 조회 차단, Kill Switch 감사 주체에서 토큰 원문 제거, API 라우트 중복 제거와 구조 검사를 적용했다.

현재 인증 쿠키는 Phase 0의 임시 정적 토큰 방식이다. 서명·만료 세션, CSRF, 비밀번호 해시, 로그인 실패 제한이 구현되기 전에는 외부 공개 MVP가 완료된 것으로 판단하지 않는다.

## 단계별 구현 범위

### Phase 0. API 계층 전면 차단

현재 거의 모든 API가 무인증이므로 다른 공개 기능보다 먼저 적용한다.

보호 대상:

- `/api/order`, `/api/overseas/order`
- `/api/balance`와 계좌/주문 이력
- `/api/system/*`
- `/api/background/*/force-update`
- `/api/position-sizing/limits`
- `/api/kill-switch/*`
- `/api/scheduler/*`
- 실시간 구독 시작·해제, 저장 등 상태를 변경하는 API

인증 없이 허용할 경로는 deny-by-default 정책의 명시적 allowlist로 관리한다.

- `POST /api/auth/login`
- 정적 파일
- 별도로 검토해 승인한 공개 데모 조회 API
- 서비스 상태를 노출하지 않는 최소 health check

구현 방식:

- 공통 인증을 적용하기 전 긴급 보정으로 로그인과 `check_auth()`가 모두 `ctx.full_config.auth`를 단일 설정 원천으로 사용하게 한다. broker 요청용 `ctx.env.active_config`에 웹 인증 정보를 추가하지 않는다.
- Phase 1의 세션 인증을 도입하면 `ctx.full_config.auth` 직접 접근도 전용 인증 설정·서비스 경계로 이동한다.
- 기대 토큰, 인증 설정 또는 요청 토큰이 누락된 경우에는 비교하지 않고 즉시 `401`로 거부한다. `None == None`과 같은 값 비교로 인증이 성공해서는 안 된다.
- `/api/*` 라우터에 공통 FastAPI dependency를 적용한다.
- 경로 문자열 비교 미들웨어만으로 보호 범위를 결정하지 않는다. 라우터 밖에 등록된 API가 생겨도 테스트에서 탐지할 수 있도록 보호 정책을 라우터 등록 구조에 둔다.
- 통합 라우터와 별도로 등록된 `operator_dashboard_router` 중복을 제거하고, 모든 API 라우트가 하나의 보호 경계를 통과하게 한다.
- `/balance` 페이지는 인증 성공 후에만 계좌 조회와 `initial_data` 생성을 수행한다. 인증 실패 요청에서는 account balance 서비스와 foreground priority를 호출하지 않는다.
- 기존 `kill_switch.py`의 개별 `check_auth()`는 공통 인증 적용 후 중복을 제거하되, 보호 정책 회귀 테스트의 기준으로 사용한다.
- Kill Switch 감사 주체는 쿠키·토큰 원문이 아닌 세션에서 해석한 비민감 운영자 식별자를 사용한다.
- `web_api.py`가 재노출하는 `check_auth` 호환 레이어도 함께 정리한다.

미들웨어의 논리적 요청 처리 순서는 다음과 같이 고정한다.

1. 요청 추적 ID 등록
2. 인증 및 상태 변경 요청의 CSRF 검증
3. foreground priority 획득
4. 라우트 실행
5. priority 해제 및 요청 추적 종료

인증 실패 요청이 broker 우선순위 자원을 획득하지 않게 해야 한다. Starlette의 미들웨어 래핑 순서와 코드 등록 순서는 다를 수 있으므로 실제 실행 순서를 통합 테스트로 고정한다. 개발용 debugpy 미들웨어는 운영 환경에서 비활성화한다.

대상 파일:

- `view/web/api_common.py`
- `view/web/web_api.py`
- `view/web/web_main.py`
- `view/web/routes/__init__.py`
- `view/web/routes/kill_switch.py`
- `view/web/routes/operator_dashboard.py`
- `view/web/bootstrap/config_bootstrap.py` 또는 신규 인증 설정·서비스 경계

### Phase 1. 공개 안전 MVP

#### 1. 인증 세션 교체

MVP는 역할 시스템을 도입하지 않고 단일 운영자 인증 유무만 구분한다. 그러나 현재의 정적 `secret_key` 쿠키는 그대로 사용하지 않는다.

- 로그인 성공 시 서명되고 만료 시간이 있는 세션을 발급한다.
- 쿠키는 `HttpOnly`, `Secure`, 적절한 `SameSite` 속성을 사용한다.
- 로그아웃과 세션 키 회전 시 기존 세션을 무효화할 수 있게 한다.
- 상태 변경 요청은 CSRF 토큰을 검증한다.
- 비밀번호는 평문 비교 대신 검증 가능한 해시로 저장한다.
- 로그인 IP 또는 계정 기준 실패 횟수 제한과 지수형 지연 또는 짧은 lockout을 적용한다.
- 인증 실패 메시지는 사용자 존재 여부를 구분해 노출하지 않는다.
- 프록시 환경에서는 신뢰할 프록시가 설정된 경우에만 전달 IP 헤더를 사용한다.

대상 파일:

- `view/web/routes/auth.py`
- `view/web/api_common.py`
- `view/web/web_main.py`
- `config/config.yaml.example`

#### 2. 공개 모드 설정

```yaml
deployment:
  public_mode: false
  demo_mode: false
  allow_live_trading: false
```

`deployment.allow_live_trading`을 국내·해외 실전 주문의 **단일 전역 master gate**로 사용한다. 기존 `overseas_stock.allow_live_trading`은 설정 이원화를 막기 위해 폐기한다.

마이그레이션 기간에는 해외 실전 주문에 두 플래그가 모두 `true`일 때만 허용해 fail-close를 유지하고, 설정 변환과 테스트가 끝나면 `overseas_stock.allow_live_trading`을 제거한다. 국내 주문에도 동일한 전역 gate를 적용한다.

`public_mode=true`의 동작:

- `deployment.allow_live_trading` 값과 관계없이 국내·해외 실전 주문 차단
- 자동매매 백그라운드 태스크 비활성화
- 서버 종료·재시작과 강제 배치 실행 비활성화
- 계좌, 잔고, 주문 정보 마스킹

대상 파일:

- `config/config.yaml.example`
- `config/config_loader.py`
- `view/web/web_app_initializer.py`
- `view/web/routes/order.py`

#### 3. 실전 주문 Fail-Close

실전 주문 허용 조건:

- 유효한 인증 세션
- `public_mode=false`
- `deployment.allow_live_trading=true`
- Kill Switch 정상
- Risk Gate 통과
- 요청의 `real_order_confirmation == "REAL"`
- 해외 설정 마이그레이션 중에는 `overseas_stock.allow_live_trading=true`

조건 확인은 국내·해외 라우트에서 중복 구현하지 않고 주문 도메인 경계에서 공통 정책으로 실행한다. 설정 누락, 파싱 오류, 정책 서비스 오류는 모두 주문 차단으로 처리한다.

정책 차단은 `ORDER_POLICY_BLOCKED` 또는 명확한 `403` 응답을 사용하고, 응답에는 민감 정보를 제외한 차단 사유를 포함한다.

대상 파일:

- `view/web/routes/order.py`
- `services/order_execution_service.py`
- `common/types.py`

#### 4. 민감 정보 마스킹

마스킹 대상:

- 계좌번호: 뒤 2~4자리만 표시
- 평가금액, 현금, 총자산: 공개 모드에서 숨김 또는 범주화
- 주문번호와 체결번호: 일부 마스킹
- 토큰, API 키, secret, 원문 broker 응답: 응답과 로그에 출력 금지

적용 경로:

- `/api/balance` 등 JSON 응답
- `web_main.py`의 `/balance` 페이지 `initial_data`
- 시스템 상태·디버그 API
- 예외 로그, 설정 로그, 요청 추적 로그

마스킹 전 원본 객체를 변경해 내부 주문 처리에 영향을 주지 않도록 응답 직렬화 경계에서 처리한다.

대상 파일:

- `view/web/web_main.py`
- `view/web/routes/balance.py`
- `view/web/routes/system.py`
- `view/web/api_common.py`
- `core/logger.py` 또는 관련 로그 호출부

#### 5. 위험한 운영 엔드포인트 비활성화

우선 대상:

- `POST /api/system/shutdown`
- `POST /api/system/restart`
- `POST /api/background/*/force-update`
- `GET/POST /api/position-sizing/limits`
- 스케줄러 시작·중지·상태 변경 API
- Kill Switch 상태 변경 API

MVP에서는 모두 인증을 요구하고, `public_mode=true`에서는 서버 종료·재시작과 강제 배치 실행을 설정과 관계없이 차단한다. 역할 기반 세분화 전까지 운영 제어 권한은 인증된 단일 운영자에게만 있다.

대상 파일:

- `view/web/routes/system.py`
- `view/web/routes/scheduler.py`
- `view/web/routes/kill_switch.py`

#### 6. SSE 및 WebSocket 인증

- `streaming.py`와 `program.py`의 SSE 엔드포인트는 공통 HTTP 인증 dependency의 보호 범위에 포함한다.
- WebSocket은 HTTP 미들웨어에 의존하지 않고 handshake 전에 세션을 직접 검증한다.
- 인증 실패 WebSocket은 연결을 accept하지 않고 정책 위반 코드로 종료한다.
- 현재 테스트용 `/ws/echo`는 운영·공개 환경에서 제거하거나 비활성화한다.
- 구독 시작·해제 API는 조회 API가 아니라 서버 상태 변경 API로 분류한다.

대상 파일:

- `view/web/routes/streaming.py`
- `view/web/routes/program.py`
- `view/web/api_common.py`

### Phase 2. 역할 기반 권한 분리

역할 분리는 사용자 저장소, 역할을 포함한 세션, 로그인 발급 로직, 권한 정책이 필요한 별도 기능이다. 공개 안전 MVP 완료 조건에는 포함하지 않는다.

역할:

- `viewer`: 공개 조회와 데모 데이터 조회
- `operator`: 모의 주문, 스케줄러 조회와 제한된 운영 작업
- `admin`: 실전 주문 허용 작업, 서버 제어, 한도 변경, 강제 배치 실행

정책:

- 주문 API는 최소 `operator` 권한 필요
- 실전 주문과 서버 제어 API는 `admin` 권한 필요
- `public_mode=true`에서는 `admin`도 실전 주문과 금지된 운영 작업을 실행할 수 없음
- 권한 변경과 위험 작업은 감사 로그에 남김

대상 후보:

- 사용자 저장소 및 비밀번호 해시 저장 구조
- `view/web/routes/auth.py`
- `view/web/api_common.py`
- 주문·시스템·스케줄러 라우트

### Phase 3. 공개 데모와 배포 분리

공개 포트폴리오가 목적이면 실제 증권사 API와 자격 증명을 사용하지 않는 데모 서비스를 별도로 배포한다.

초기 범위:

- 샘플 현재가, 잔고, 가상매매 이력
- `demo_mode=true`일 때 외부 broker 호출 금지
- 주문 API는 차단하거나 가상 주문만 기록
- 공개 데모와 운영 서버가 같은 데이터베이스, 계정, secret을 공유하지 않게 구성

GitHub Pages UI와 FastAPI API를 다른 origin으로 분리할 경우:

- CORS는 `*`가 아닌 정확한 GitHub Pages origin만 허용
- 허용 method와 header를 최소화
- credential 사용 여부를 명시하고 CSRF 및 쿠키 `SameSite` 정책과 함께 설계
- 가능하면 인증이 필요한 운영 앱은 reverse proxy로 same-origin 유지

대상 후보:

- `data/demo/`
- `services/demo_market_data_service.py`
- `view/web/bootstrap/service_container.py`
- `view/web/web_main.py`

## TDD 계획

### Phase 0 단위·통합 테스트

- 인증 없는 민감 API 요청은 `401` 또는 `403`을 반환한다.
- 로그인 API와 승인된 공개 API만 인증 없이 접근할 수 있다.
- 인증 설정, 기대 토큰 또는 요청 쿠키가 누락되면 `check_auth()`는 반드시 `401`을 반환한다.
- 로그인 발급과 API 검증이 동일한 인증 설정 원천을 사용한다.
- 비인증 `/balance` 페이지 요청은 계좌 조회 서비스와 foreground priority를 호출하지 않는다.
- Kill Switch 감사 로그와 서비스 호출 인자에 쿠키 또는 토큰 원문이 포함되지 않는다.
- 인증 실패 요청은 foreground priority를 획득하지 않는다.
- 새 `/api/*` 라우트가 보호 정책 없이 등록되거나 동일 API 라우트가 중복 등록되면 테스트가 실패한다.
- SSE는 인증 세션을 요구하고, WebSocket은 handshake 인증 실패 시 연결되지 않는다.

### Phase 1 단위 테스트

- 세션은 위·변조되거나 만료되면 거부된다.
- 상태 변경 요청은 CSRF 토큰이 없거나 일치하지 않으면 거부된다.
- 반복 로그인 실패 시 제한이 적용되고 정상 로그인 후 정책대로 초기화된다.
- `public_mode=true`에서는 국내·해외 실전 주문이 차단된다.
- 실전 모드에서는 전역 live trading gate와 `"REAL"` 확인 문자열이 모두 필요하다.
- 해외 플래그 마이그레이션 중 하나라도 `false`이면 해외 실전 주문이 차단된다.
- 계좌 정보는 JSON 응답과 `/balance`의 `initial_data`에서 모두 마스킹된다.
- 공개 모드에서 서버 제어와 강제 배치가 차단된다.

### Phase 2 테스트

- `viewer`, `operator`, `admin`별 허용·거부 행렬을 검증한다.
- `public_mode` 차단 정책이 역할보다 우선한다.
- 권한 변경과 위험 작업 감사 로그를 검증한다.

### 통합 테스트 대상

- 공개 모드 FastAPI e2e smoke
- 로그인, 세션 만료, 로그아웃, CSRF 흐름
- 인증 쿠키 포함·미포함 API 접근
- 공개 모드에서 background force-update 차단
- demo mode에서 외부 broker 호출 없이 샘플 응답 반환
- 허용 origin과 비허용 origin의 CORS 동작

예상 테스트 파일:

- `tests/unit_test/view/web/routes/test_web_routes_auth.py`
- `tests/unit_test/view/web/routes/test_web_routes_order.py`
- `tests/unit_test/view/web/routes/test_web_routes_system.py`
- `tests/unit_test/view/web/routes/test_web_routes_balance.py`
- `tests/integration_test/test_it_web_api_paper.py`
- `tests/integration_test/test_it_web_app_e2e_smoke.py`

## 추천 구현 순서

1. `check_auth()` fail-open, 인증 전 `/balance` 조회, API 중복 등록 긴급 보정
2. Phase 0 공통 API 인증과 보호 경계 테스트
3. 서명·만료 세션, CSRF, 로그인 실패 제한
4. `public_mode`와 단일 전역 live trading gate
5. 국내·해외 주문 fail-close 통합
6. JSON·서버 렌더·로그 마스킹
7. 운영 엔드포인트와 SSE·WebSocket 보호
8. 배포 runbook과 공개 환경 검증
9. Phase 2 역할 기반 권한
10. Phase 3 데모 데이터 및 배포 분리

## MVP 완료 기준

Phase 0과 Phase 1을 공개 안전 MVP로 정의한다.

- 인증 없이 주문, 잔고, 시스템 제어, 강제 배치, 구독 상태 변경 API를 호출할 수 없다.
- 인증 설정이나 쿠키가 누락돼도 fail-open되지 않으며, 로그인과 API 검증이 동일한 인증 설정을 사용한다.
- 비인증 페이지 요청은 계좌 조회 등 보호된 broker 작업을 먼저 실행하지 않는다.
- 모든 API 라우트가 하나의 인증 경계에 등록되고 중복 API 라우트가 없다.
- SSE와 WebSocket을 HTTP 페이지 우회로 사용할 수 없다.
- 정적 `secret_key` 원문 쿠키를 사용하지 않으며 세션 만료, 로그아웃, CSRF 방어가 동작한다.
- 인증 토큰 원문이 운영자 식별자, 감사 로그 또는 상태 저장소에 사용되지 않는다.
- 반복 로그인 시도에 제한이 적용된다.
- 공개 모드에서 국내·해외 실전 주문은 설정 실수와 관계없이 차단된다.
- 국내·해외 주문이 하나의 전역 live trading gate를 사용한다.
- JSON 응답, 서버 렌더 컨텍스트, 로그에 계좌번호, 토큰, API secret, 원문 주문 정보가 노출되지 않는다.
- Kill Switch와 Risk Gate가 기존대로 동작한다.
- 관련 단위 테스트와 전체 통합 테스트가 통과한다.

역할 기반 권한과 사용자 저장소는 Phase 2 완료 기준으로 별도 관리한다.

## 검증 명령

```powershell
pytest tests/unit_test/view/web/routes -v
pytest tests/unit_test -v
pytest tests/integration_test -v
```

순수 문서 변경만 수행한 경우 테스트는 생략할 수 있다.

## 배포 Runbook 후속 문서

`docs/public_deployment_runbook.md`에는 다음 내용을 포함한다.

- 서버 환경 변수와 secret 저장 방식
- `config.yaml`과 토큰 파일 배포 금지 규칙
- HTTPS와 reverse proxy 설정
- trusted host와 proxy header 정책
- CORS exact allowlist
- 쿠키와 CSRF 설정
- 공개 모드 및 실전 주문 차단 체크리스트
- 배포 전 테스트 명령
- 세션 키와 API 키 회전 절차
- 비상 중지 및 복구 절차
