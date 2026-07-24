# 외부 공개 배포 Runbook

## 배포 방식

이 앱의 FastAPI 서버는 GitHub Pages에서 실행할 수 없다. GitHub Pages는 정적 HTML, CSS, JavaScript만 제공하므로 FastAPI는 별도 서버나 클라우드에 배포해야 한다.

운영 앱은 다음 구성을 권장한다.

```text
Internet -> HTTPS reverse proxy -> FastAPI (127.0.0.1:8000)
```

로컬 PC의 포트와 방화벽을 인터넷에 직접 개방하지 않는다. 클라우드 VM 또는 사설 서버에서 reverse proxy를 두고 FastAPI는 loopback에만 바인딩한다. 공개 UI와 API는 가능한 한 같은 origin으로 유지한다.

## 사전 조건

- Python 3.10 이상과 프로젝트 의존성 설치
- 모의투자 전용 API 키와 계좌 사용
- `config/config.yaml` 및 `token_*.json`을 저장소와 이미지에 포함하지 않음
- HTTPS 인증서가 적용된 reverse proxy
- 단일 Uvicorn worker

현재 세션 무효화 목록과 로그인 실패 제한은 프로세스 메모리에 저장된다. 여러 worker나 여러 인스턴스를 사용하면 상태가 공유되지 않으므로 Phase 1에서는 worker를 하나만 사용한다.

## 보안 설정

1. 비밀번호 해시를 생성한다.

```powershell
python scripts/hash_web_password.py
```

2. 최소 32자의 예측 불가능한 세션 서명 키를 별도로 생성한다.

3. `config/config.yaml`에 다음 값을 설정한다.

```yaml
is_paper_trading: true
use_login: true

deployment:
  public_mode: true
  demo_mode: false
  allow_live_trading: false
  allowed_hosts:
    - "trade.example.com"

web:
  host: "127.0.0.1"
  port: 8000

auth:
  users:
    - username: "reader"
      password_hash: "pbkdf2_sha256$..."
      role: "viewer"
      enabled: true
    - username: "operator"
      password_hash: "pbkdf2_sha256$..."
      role: "operator"
      enabled: true
    - username: "admin"
      password_hash: "pbkdf2_sha256$..."
      role: "admin"
      enabled: true
  secret_key: "32자 이상의 무작위 비밀"
  session_max_age_seconds: 3600
  login_max_failures: 5
  login_lockout_seconds: 60
  secure_cookie: true
```

각 사용자는 서로 다른 비밀번호와 해시를 사용한다. `public_mode=true`에서는 활성화된 admin, 모든 활성 사용자의 PBKDF2 비밀번호 해시, 32자 이상 서명 키, secure cookie와 명시적 allowed host가 없으면 설정 로드가 실패한다. `allowed_hosts: ["*"]`는 허용되지 않는다.

기존 `auth.username`과 `auth.password_hash` 단일 계정은 마이그레이션을 위해 admin으로 해석되지만 신규 배포에서는 `auth.users`를 사용한다.

### 역할 운영

- `viewer`: 시세, 차트, 랭킹과 전략 리포트 조회
- `operator`: viewer 권한과 잔고·운영 상태 조회, 모의 주문, 일반 구독 작업
- `admin`: operator 권한과 실전 주문, 서버·Kill Switch·스케줄러 제어, 강제 배치와 한도 변경

`public_mode`의 실전 주문 및 위험 작업 차단은 admin보다 우선한다. 사용자 설정을 변경한 뒤 서비스를 재시작한다. 재시작은 메모리 세션을 모두 제거하며, 실행 중 설정에서 사용자가 비활성화되거나 role이 달라진 경우에도 다음 요청에서 해당 세션을 폐기한다.

권한 거부와 admin 작업의 권한 결정은 `rbac_authorization` 감사 로그로 남는다. 비밀번호 해시와 세션 토큰은 로그에 기록하지 않는다.

## Reverse Proxy

- 외부에서는 HTTPS만 허용하고 HTTP는 HTTPS로 리다이렉트한다.
- FastAPI 포트는 외부 방화벽에서 차단하고 reverse proxy에서만 접근시킨다.
- 요청 본문 크기, 연결 시간과 읽기 시간 제한을 둔다.
- 로그인과 API 경로에 추가 rate limit을 적용한다.
- 전달 IP 헤더는 신뢰하는 reverse proxy 한 단계에서만 설정한다.

앱은 기본적으로 직접 연결된 client IP를 로그인 제한 키로 사용한다. 임의 클라이언트가 보낸 `X-Forwarded-For`를 신뢰하도록 Uvicorn의 proxy header 범위를 넓히지 않는다.

별도 GitHub Pages UI처럼 다른 origin을 사용할 경우 현재 구성 그대로는 지원 대상이 아니다. Phase 3에서 정확한 origin만 허용하는 CORS 정책, credential cookie와 CSRF 동작을 함께 설계한다. 운영 앱에는 `Access-Control-Allow-Origin: *`를 사용하지 않는다.

## 배포 전 검증

```powershell
pytest tests/unit_test -v
pytest tests/integration_test -v
git status -sb
```

### PR Merge 확인

PR은 Draft가 해제되고 필수 CI와 review check가 모두 성공한 경우에만 merge 후보가 된다. 추가로 충돌 없음, 필수 승인 충족, 해결되지 않은 review thread 없음과 GitHub의 merge 가능 판정을 확인한다.

`pending`, `failure`, `cancelled`, `mergeable=UNKNOWN` 상태에서는 merge하지 않는다. 모든 조건이 충족되면 저장소 기본 merge 방식을 사용해 merge할 수 있다. Merge 후에는 로컬 `main`을 `origin/main`과 동기화하고 최종 커밋과 check 결과를 확인한다.

PR merge와 운영 배포는 별도 승인 단계다. CI가 통과한 PR을 merge했더라도 공개 서버 설정, secret, HTTPS와 복구 절차 확인 없이 자동 배포하지 않는다.

브라우저와 별도 HTTP 클라이언트에서 다음을 확인한다.

- 비로그인 API 요청이 `401` 또는 `403`
- 변조되거나 만료된 세션이 거부됨
- CSRF 헤더 없는 상태 변경 요청이 거부됨
- 등록하지 않은 Host 요청이 `400`
- 국내·해외 실전 주문이 공개 모드에서 차단됨
- 종료, 재시작, force-update, scheduler, limits, kill-switch 변경이 차단됨
- 잔고 페이지와 JSON, 시스템 상태 및 로그에 계좌·금액·토큰이 노출되지 않음
- WebSocket과 SSE가 로그인 세션 없이 연결되지 않음

## 운영 및 비상 대응

세션 서명 키를 교체하면 기존 세션은 모두 무효화된다. 비밀번호 또는 세션 키 유출이 의심되면 서비스를 외부에서 먼저 격리하고 키와 비밀번호를 교체한 뒤 재시작한다.

증권사 API 키가 노출된 경우에는 애플리케이션 설정만 바꾸지 말고 증권사에서 키를 폐기·재발급한다. 관련 토큰 파일과 로그도 격리한다.

비정상 주문 징후가 있으면 reverse proxy 접근을 차단하고 프로세스를 중지한 뒤 증권사 채널에서 미체결 주문과 계좌 상태를 직접 확인한다. 공개 모드의 Kill Switch 변경 API는 차단되므로 원격 API 호출을 비상 중지 수단으로 간주하지 않는다.

복구 시에는 모의투자, `public_mode=true`, `allow_live_trading=false`로 먼저 기동한다. 인증, 마스킹, 위험 작업 차단 검증을 다시 통과한 뒤에만 공개 접근을 복원한다.
