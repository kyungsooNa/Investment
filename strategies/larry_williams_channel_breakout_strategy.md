🦅 래리 윌리엄스 채널 돌파 (Larry Williams / Brent Penfold Donchian Channel Breakout)
핵심 철학: "예측하려 하지 마라. 시장이 신고가를 쓴다는 것은 그 자체가 가장 강력한 매수 신호다. 대신 자금 관리를 통해 파산을 원천 차단하라." — 브렌트 펜볼드

출처: 래리 윌리엄스의 채널 돌파 규칙(20일 고가 돌파 진입, 10일 저가 trailing stop)과 브렌트 펜볼드의 돈천(Donchian) 채널 + Fixed Fractional 자금관리 원칙을 통합한 전략이다. LarryWilliamsVBOStrategy(당일 변동성 돌파, 오버나이트 금지)와 달리 **일봉 스윙 포지션** 으로 운영한다.

1. 🚀 페이즈 1: 시장 에너지 검증 (Setup)
시장 강도와 추세 방향이 모두 확인된 종목만 감시망에 둡니다.

WatchList: OneilUniverseService의 Pool A (전일 기준 우량주) 만을 사용. Pool B(당일 거래대금 급등주)는 사용하지 않음.

RS Rating 필터: OSBWatchlistItem.rs_rating ≥ 80. (시장 대비 상대강도 상위 20% 이상 — Penfold 원안)

추세 강도 필터: ADX(14) ≥ 25 이며 최근 3일 ADX 값이 우상향 중일 것. (ADX < 25 는 횡보장 — 채널 돌파의 잦은 손절 방지)
- IndicatorService.calc_adx_sync(ohlcv, period=14) 사용 예정 (신규 구현 필요)
- ADX 기울기: adx[-1] > adx[-4] (3봉 이전 대비 상승이면 충족)

MA 정배열 조건: 적용하지 않음. ADX 가 추세 검증 역할을 하므로 별도 MA 정배열 조건 불필요. (MinerviniStage 조건 미사용 — RSI2PullbackStrategy 와 차이점)

유동성 가드: Pool A 편입 기준에서 avg_trading_value_5d 가 보장됨. 별도 추가 필터 불필요.

2. 🎯 페이즈 2: 채널 돌파 진입 (Trigger)
신고가 돌파라는 사실만 믿고, 다른 해석은 개입시키지 않습니다. 단, 에너지(거래량)가 동반된 돌파만 인정합니다.

진입 시각: 15:10 이후 종가 베팅 진입. (ADX/거래량 필터가 일봉 확정값에서 가장 신뢰도 높음. 장중 1초 단위 폴링 불필요)

채널 상단 돌파 조건: 당일 종가 > 최근 20거래일 최고가(high_20d). (OSBWatchlistItem.high_20d 재사용)
- high_20d 는 **어제까지의** 20일 고가. 오늘 종가가 이를 초과하면 신고가 돌파로 판정.

거래량 확인: 당일 거래량 ≥ OSBWatchlistItem.avg_vol_20d × 1.5. (20일 평균 거래량의 150% 이상 — 수급 동반 확인)

포지션 사이징 (Fixed Fractional — 핵심):
$$수량 = \frac{총자산 \times 0.015}{진입가 - 손절가}$$
단일 종목 손실이 전체 자산의 1.5%를 넘지 않도록 수량을 자동 결정.
- 전략은 TradeSignal에 동적 stop_loss_pct = (hard_stop − 진입가) / 진입가 × 100 을 부여한다.
- StrategyScheduler가 PositionSizingService.adjust_buy_qty(signal) 를 호출해 risk_qty / cap_qty / cash_qty 4-way min으로 최종 수량을 결정한다.
- per_trade_risk_pct(기본 1.5)는 글로벌 PositionSizingConfig에서 관리되며 전략별 Config가 아니다.
- 손절가: max(직전 20일 채널 하단, 진입가 × 0.93) — 아래 페이즈 3 참조.

중복 진입 금지: 동일 종목 보유 중이면 추가 매수 금지. 청산 후 최소 2거래일 쿨다운.

3. ✂️ 페이즈 3: 추세 추종 청산 (Exit)
익절은 시장에 맡기되, 손절은 기계적으로 집행합니다.

트레일링 채널 스탑 (주 청산 수단): 장중 현재가 < 최근 10거래일 최저가(channel_low_10d) 일 때 즉시 매도 (라이브 실행은 폴링 기반 실시간 감지). channel_low_10d는 매 청산 검사 시 OHLCV 재조회로 상향만 갱신.
- channel_low_10d 는 보유 기간 중 매 장마감 후 재계산하여 LarryWilliamsCBPositionState 에 갱신.
- 수익이 날수록 channel_low_10d 가 상향되어 자동으로 익절선이 올라오는 구조.

칼손절 (진입 직후 고정 스탑): 진입 당일 확정. 이후 변경하지 않음.
- 손절가 = max(직전 20일 채널 하단 종가, 진입가 × (1 + hard_stop_pct))
- 예: 진입가 10,000원, 20일 채널 하단=9,500원, 진입가×0.93=9,300원 → 손절가=9,500원 (더 짧은 쪽)
- 장중 현재가가 손절가 이하이면 즉시 시장가 매도. (EOD 점검 외 장중 체크 병행)

우선순위: 칼손절 > 트레일링 스탑. 칼손절 도달 시 channel_low_10d 점검 생략.

EOD 점검 루틴 (check_exits): 매 장마감 후 보유 포지션 전체를 순회.
1. 칼손절 도달 여부 확인 → 도달 시 다음 장 시초가 매도 신호
2. 종가 vs channel_low_10d 비교 → 이탈 시 다음 장 시초가 매도 신호
3. 미이탈 시 channel_low_10d 를 재계산(최근 10일 저가)하여 상향 갱신

4. ⚙️ 파라미터 요약
| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `channel_high_period` | 20 | 진입 기준 채널 상단 기간 (일봉) |
| `channel_low_period` | 10 | trailing stop 기준 채널 하단 기간 (일봉) |
| `adx_period` | 14 | ADX 계산 기간 |
| `adx_threshold` | 25 | ADX 최소값 (25 이상 = 유효 추세) |
| `adx_slope_lookback` | 3 | ADX 우상향 판단 기간 (봉 수) |
| `volume_multiplier` | 1.5 | 당일 거래량 / 20일 평균 거래량 최소 배수 |
| `rs_rating_min` | 80 | RS Rating 최소값 (1~99) |
| `risk_per_trade_pct` | 1.5 | Fixed Fractional 단일 거래 손실 허용 비중 (%) |
| `hard_stop_pct` | -7.0 | 진입가 대비 칼손절 하한 (%) |
| `entry_cutoff_time` | 15:10 | 종가 베팅 진입 시작 시각 |
| `reentry_cooldown_days` | 2 | 청산 후 동일 종목 재진입 차단 일수 |
| `max_positions` | 5 | 전략 내 최대 동시 보유 종목 수 |

5. 🔒 전략 불변 원칙 (Invariants)
- 스윙 포지션: 오버나이트 허용. LarryWilliamsVBOStrategy 와 달리 당일 청산 강제 없음.
- 트레일링 스탑만 상향: channel_low_10d 는 보유 기간 중 상향만 허용. 하향 수정 금지.
- Fixed Fractional 우선: 수량보다 리스크 금액 제어가 먼저. 계좌 잔고와 무관하게 1.5% 룰 준수.
- 1종목 1포지션: 보유 중 동일 종목 추가 매수(물타기/피라미딩) 금지.
- ADX 확인 생략 금지: ADX 계산 실패(데이터 부족 등) 시 해당 종목 skip. 0으로 대체 금지.
- LiveStrategy 인터페이스 준수: scan() / check_exits(holdings) 두 메서드만 외부에 노출.

6. 🧩 의존 서비스 / 인터페이스
- interfaces/live_strategy.py — LiveStrategy 베이스 (scan, check_exits)
- services/oneil_universe_service.py — get_watchlist (Pool A), OSBWatchlistItem.rs_rating / high_20d / avg_vol_20d
- services/indicator_service.py — calc_adx_sync(ohlcv, period=14) ← **신규 구현 필요**
- services/stock_query_service.py — get_ohlcv_daily() (최근 20일 일봉, trailing stop 재계산용)
- services/position_sizing_service.py — adjust_buy_qty() (Fixed Fractional, per_trade_risk_pct=1.5)
- strategies/larry_williams_cb_types.py — LarryWilliamsCBConfig, LarryWilliamsCBPositionState (신규)
- strategies/oneil_common_types.py — OSBWatchlistItem

7. 🧪 백테스트 / 검증 메모
- 데이터: 일봉 OHLCV (Pool A 종목 한정, RS Rating ≥ 80 필터링 적용).
- 측정: 평균 보유일수, 승률, 평균 손익비, MDD, 20일 채널 돌파 + ADX ≥ 25 동시 충족 빈도.
- 시나리오: 강세장(2020~2021), 박스권(2022 상반기), 약세장(2022 하반기) 구간별 성과 분리.
  - ADX 필터 ON/OFF 비교: 횡보장에서의 손절 빈도 차이 확인.
  - volume_multiplier 1.5 → 1.2 완화 시 신호 빈도 vs 성과 트레이드오프.
- mock 백테스트 케이스: scan() 단위 테스트로 채널 상단 돌파 + ADX ≥ 25 + 거래량 조건 시그널 트리거/미트리거를 단위 검증 후 실데이터로 확장.

8. ⚠️ 알려진 갭 / 향후 보완
- ADX calc_adx_sync 신규 구현: 구현 전까지 ADX 조건을 비활성화(항상 True)하고 나머지 조건만으로 운영하는 임시 모드 선택지 있음. 단, 실전 투입 전 ADX 활성화 필수.
- channel_low_10d 는 OSBWatchlistItem 에 없으므로 check_exits 에서 OHLCV API 를 추가 호출해 계산. API 부하 모니터링 필요.
- 장중 칼손절 감지: LarryWilliamsVBOStrategy 처럼 scan() 내에서 보유 중 종목의 현재가를 확인해 칼손절 조건 장중 조기 감지 검토. (현재 설계는 EOD check_exits 위주)
- 포트폴리오 백테스트 필요: Pool A 전체 동시 신호 시 max_positions 우선순위 규칙(RS Rating 높은 순) 검증.
