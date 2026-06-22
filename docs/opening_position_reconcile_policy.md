# Opening Position Reconcile Policy

`OpeningPositionReconcileTask`는 장 시작 직후 broker 계좌 잔고와 로컬 가상 원장(`virtual_trade.db`)의 보유 상태를 대사한다.

이 정책의 기준 truth는 broker 계좌 잔고다. 로컬 원장은 broker 잔고에 맞춰 보수적으로 정정하며, broker에 실제 주문을 발주하지 않는다.

## 실행 시점

- 장 시작 시각 + `open_delay_sec` 이후 실행한다.
- 기본 실행 윈도우는 10분이다.
- 정상 대사 결과를 받은 날에는 하루 1회만 실행한다.
- broker 잔고 조회 실패처럼 `error`가 있는 결과는 날짜 stamp를 남기지 않아, 같은 윈도우 안에서 다음 tick에 재시도할 수 있다.

## 정정 규칙

| Case | Behavior |
| --- | --- |
| 로컬 `HOLD`, broker 수량 0 | 로컬 `HOLD` row를 강제 종결한다. |
| broker 수량 > 0, 로컬 `HOLD` 없음 | `broker_reconciled` 전략 `HOLD`로 로컬 원장에 자동 등록하고 경고를 남긴다. |
| 양쪽 모두 보유하지만 수량 불일치 | 자동 정정 없이 경고만 남긴다. |
| 양쪽 수량 일치 | 아무 작업도 하지 않는다. |

강제 종결은 `VirtualTradeService.reconcile_with_broker()`가 수행한다.
로컬에만 있는 종목은 해당 code의 `HOLD` row 수만큼 `log_sell_async(code, 0, reason="reconciled_force_close")`를 호출한다.

## Force Close

강제 종결된 거래는 다음 값으로 기록된다.

```text
status = SOLD
sell_price = 0
reason = reconciled_force_close
```

`VirtualTradeRepository.get_summary()`는 `reason="reconciled_force_close"` 거래를 승률과 평균수익률 계산에서 제외하고, `force_closed_count`로 별도 집계한다.

## Broker-only 자동 등록

broker에는 있으나 로컬 원장에 `HOLD`가 없는 종목(`unknown_in_broker`)은 `broker_reconciled` 전략명의 `HOLD` row로 자동 등록한다. 등록 시 broker 잔고의 평균매입가(`pchs_avg_pric`)를 `buy_price`로 사용하며, 값이 없으면 `0.0`으로 기록한다.

이전에는 전략명을 알 수 없어 경고만 남기고 수동 등록을 기다렸으나, 그 결과 등록 전까지 매 영업일 아침 동일 대사 경보가 반복됐다. 자동 등록은 발견 당일 1회 경보 후(이후 영업일부터는 로컬에 존재하므로) 경보가 해소되도록 한다. broker에 실제 주문을 내는 것이 아니라 로컬 원장만 정정하며, 등록된 포지션은 사후에 올바른 전략으로 재지정할 수 있다.

```python
{
    "broker_inserted": [
        {"code": "035420", "qty": 3, "buy_price": 50000.0},
    ],
}
```

## 수량 불일치

수량 불일치는 자동 정정하지 않는다.

partial close가 필요한 경우 어떤 lot 또는 어떤 전략의 `HOLD`를 닫아야 하는지 결정해야 한다. 현재 repository의 일반 매도 경로는 같은 code의 최신 `HOLD` row를 닫는 LIFO 방식이므로, 수량 차이 자동 정정은 별도 설계가 필요하다.

대신 결과에 아래 형태로 포함해 알림과 로그에서 확인할 수 있게 한다.

```python
{
    "quantity_mismatches": [
        {"code": "005930", "local_qty": 3, "broker_qty": 1},
    ],
}
```

## Result Shape

`OpeningPositionReconcileService.reconcile_once()`는 task 호환을 위해 아래 형태를 반환한다.

```python
{
    "force_closed": ["005930"],
    "unknown_in_broker": ["035420"],
    "quantity_mismatches": [
        {"code": "000660", "local_qty": 3, "broker_qty": 1},
    ],
    "broker_inserted": [
        {"code": "035420", "qty": 3, "buy_price": 50000.0},
    ],
    "mismatch_count": 3,
    "error": None,
}
```

`mismatch_count`는 `force_closed`, `unknown_in_broker`, `quantity_mismatches`의 항목 수 합계다. `broker_inserted`는 자동 등록이 발생한 경우에만 포함되며, 자동 등록된 종목은 `unknown_in_broker`에도 함께 보고되어 발견 당일 1회 경보를 발생시킨다.

## Deprecated Config

아래 설정은 더 이상 사용하지 않는다.

```yaml
opening_position_reconcile:
  detect_only: true
  auto_buy_missing_local: false
  auto_sell_extra_broker: false
  allow_sell_unknown_broker: false
```

새 정책은 broker 주문을 내지 않고 로컬 원장만 보수적으로 정정한다. 따라서 기존 detect-only/auto-buy/auto-sell 플래그는 의미가 없으며, 설정 파일에 남아 있으면 시작 시 warning으로 알린 뒤 무시한다.

계속 사용하는 설정은 실행 스케줄 관련 필드다.

```yaml
opening_position_reconcile:
  enabled: true
  check_interval_sec: 30
  open_delay_sec: 60
  run_window_min: 10
```
