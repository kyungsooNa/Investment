# Risk Gate

`RiskGateService`는 broker 주문 제출 직전에 실행되는 공통 hard-block 계층이다.
주문 실행 서비스는 주문 상태 관리와 제출만 담당하고, 계좌/전략/포트폴리오 리스크 판단은 이 서비스가 담당한다.

## Decision Response

차단 응답은 `ResCommonResponse`로 반환한다.

```python
ResCommonResponse(
    rt_cd=ErrorCode.RISK_GATE_BLOCKED.value,
    msg1="Risk Gate 차단: 전략 자본 할당 한도 초과",
    data={
        "gate": "risk_gate",
        "rule": "strategy_exposure_limit",
        "severity": "block",
        "reason": "전략 자본 할당 한도 초과",
        "strategy_name": "모멘텀",
        "stock_code": "005930",
        "next_exposure_pct": 12.5,
        "max_exposure_pct": 10.0,
    },
)
```

## Logs

모든 차단은 `WARNING` 레벨로 남긴다.

```text
[RiskGate][BLOCK] rule=duplicate_strategy_position reason=동일 전략에서 이미 보유 중인 종목입니다. context={'strategy_name': '모멘텀', 'stock_code': '005930'}
```

선택 데이터 조회 실패는 fail-open으로 처리하되 진단 로그를 남긴다.

```text
[RiskGate][CHECK_ERROR] rule=strategy_loss_limit strategy=모멘텀 error=...
```

## Rules

| Rule | Scope | Block Condition |
| --- | --- | --- |
| `kill_switch_active` | account | Kill switch가 주문 불가 상태를 반환 |
| `kill_switch_check_failed` | account | Kill switch 상태 확인 실패 |
| `buy_non_positive_price` | order | BUY 주문 가격이 0 이하 |
| `max_pending_orders` | account | 진행 중 주문 수가 설정 한도 이상 |
| `max_order_amount` | order | 주문 금액이 단일 주문 한도 초과 |
| `duplicate_strategy_position` | strategy+stock | 같은 전략이 같은 종목을 이미 HOLD |
| `strategy_loss_limit` | strategy | 전략 최근 수익률이 손실 한도 이하 |
| `strategy_exposure_limit` | strategy | 전략 보유금액+신규주문금액이 전략 자본 한도 초과 |
| `max_total_exposure` | account | 계좌 전체 노출이 총자산 대비 한도 초과 |

## Duplicate Entry Policy

중복 진입 제한은 `(strategy_name, stock_code)` 단위로 적용한다.

- `모멘텀 / 005930` HOLD 상태에서 `모멘텀 / 005930` BUY: 차단
- `모멘텀 / 005930` HOLD 상태에서 `눌림목 / 005930` BUY: 허용
- 단, 다른 전략의 동일 종목 BUY도 총 노출, 전략별 노출, 섹터 집중도 같은 포트폴리오 한도는 그대로 적용받는다.

## Config Example

```yaml
risk_gate:
  enabled: true
  max_order_amount_won: 10000000
  max_pending_orders: 10
  max_total_exposure_pct: 70.0
  block_duplicate_strategy_position: true
  default_strategy_limit:
    max_exposure_pct: 20.0
    max_loss_pct: 5.0
    block_duplicate_position: true
  strategy_limits:
    모멘텀:
      max_exposure_pct: 15.0
      max_loss_pct: 4.0
    눌림목:
      max_exposure_pct: 10.0
```

`max_loss_pct`는 양수로 설정한다. 예를 들어 `5.0`은 최근 전략 수익률이 `-5.0%` 이하일 때 신규 BUY를 차단한다.

## Fail-open Data

아래 선택 데이터가 없거나 조회 실패하면 주문을 즉시 차단하지 않고 로그만 남긴다.

- 전략별 HOLD 조회
- 전략별 수익률 이력
- 전략별 노출 계산용 계좌 스냅샷

계좌 스냅샷의 `total_equity`가 0 이하일 때도 현재는 fail-open이다. 실전 운영에서 더 보수적으로 막고 싶다면 별도 설정으로 fail-close 정책을 추가한다.

## Out Of Scope

시장가/지정가, 호가단위, 슬리피지, 스프레드/호가 공백 검증은 `OrderPolicyService` 계층에서 다룬다.
