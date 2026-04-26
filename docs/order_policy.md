# Order Policy

`OrderPolicyService`는 broker 주문 제출 직전에 주문 형태와 가격 정책을 검증한다.
포트폴리오/전략 리스크는 `RiskGateService`가 담당하고, 시장가/지정가/NXT/호가단위/호가 공백/스프레드/슬리피지는 이 계층이 담당한다.

## Decision Response

차단 응답은 `ResCommonResponse`로 반환한다.

```python
ResCommonResponse(
    rt_cd=ErrorCode.ORDER_POLICY_BLOCKED.value,
    msg1="Order Policy 차단: 스프레드가 허용 범위를 초과했습니다.",
    data={
        "gate": "order_policy",
        "rule": "spread_too_wide",
        "severity": "block",
        "reason": "스프레드가 허용 범위를 초과했습니다.",
        "stock_code": "005930",
        "ask": 71000,
        "bid": 70000,
        "spread_pct": 1.418,
        "max_spread_pct": 1.0,
    },
)
```

## Logs

차단은 `WARNING` 레벨로 남긴다.

```text
[OrderPolicy][BLOCK] rule=market_slippage_too_high reason=시장가 예상 슬리피지가 허용 범위를 초과했습니다. context={...}
```

호가단위 보정은 `INFO` 레벨로 남긴다.

```text
[OrderPolicy][ADJUST] rule=tick_size stock_code=005930 requested_price=70051 adjusted_price=70000 tick_size=100
```

## Rules

| Rule | Scope | Behavior |
| --- | --- | --- |
| `non_positive_qty` | order | 주문 수량이 0 이하이면 차단 |
| `negative_price` | order | 주문 가격이 음수이면 차단 |
| `nxt_market_order_not_supported` | exchange | NXT 시장가 주문 차단 |
| `market_buy_disabled` | order type | 시장가 매수 비활성화 시 차단 |
| `market_sell_disabled` | order type | 시장가 매도 비활성화 시 차단 |
| `invalid_tick_size` | limit price | 지정가가 호가단위에 맞지 않고 block 정책이면 차단 |
| `tick_size_adjusted` | limit price | 지정가를 호가단위에 맞게 사전 보정 |
| `empty_order_book` | market order | 최우선 매도/매수 호가가 비어 있으면 차단 |
| `spread_too_wide` | market order | 최우선 매도-매수 스프레드가 설정 한도 초과 |
| `market_slippage_too_high` | market order | 예상 체결가와 기준가의 괴리가 설정 한도 초과 |
| `top_of_book_qty_short` | market order | 최우선 호가 잔량보다 주문 수량이 큼 |
| `quote_unavailable` | market order | 호가 조회 실패. 설정에 따라 fail-open 또는 block |

## Config Example

```yaml
order_policy:
  enabled: true
  allow_market_buy: true
  allow_market_sell: true
  allow_nxt_market_order: false
  tick_size_policy: adjust      # adjust | block | ignore
  order_book_checks_enabled: true
  max_market_slippage_pct: 1.0
  max_spread_pct: 1.0
  block_empty_order_book: true
  quote_fail_policy: block      # allow | block
```

## Market Order Notes

`price == 0`은 시장가 주문으로 해석한다.

- KRX 시장가 주문은 설정이 허용하면 가능하다.
- NXT 시장가 주문은 기본적으로 차단한다.
- 호가 검증을 켜면 `get_asking_price()`를 통해 최우선 매도/매수 호가와 잔량을 확인한다.
- 호가 조회 실패 시 기본값은 fail-open이지만, 실전 주문에서는 `quote_fail_policy: block`을 권장한다.

## Limit Order Notes

`price > 0`은 지정가 주문으로 해석한다.

`tick_size_policy`:

- `adjust`: 호가단위에 맞게 내림 보정 후 보정 가격으로 주문 제출
- `block`: 호가단위 불일치 시 broker 제출 전 차단
- `ignore`: 사전 검증을 건너뛰고 broker 안전망에 위임

broker 계층의 호가단위 보정은 마지막 안전망으로 유지한다.
