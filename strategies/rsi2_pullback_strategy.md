🦅 래리 코너스 RSI(2) 눌림목 (Larry Connors RSI(2) Pullback)
핵심 철학: "대세 상승장(Stage 2)에서도 주가는 숨을 고른다. RSI(2)가 10 이하라는 것은 고무줄이 팽팽하게 당겨진 상태와 같으므로 반등의 탄성이 가장 크다."

1. 🚀 페이즈 1: 주도주 및 추세 확인 (Setup)
이미 검증된 주도주가 단기 과매도에 빠진 순간만을 골라 감시망에 둡니다.

WatchList: OneilUniverseService의 Pool A (전일 기준 우량주) 만을 사용. Pool B(당일 급등주)는 본 전략에서 사용하지 않음.

장기 추세 (미너비니 Stage 2): MinerviniStageService.classify_stage() 결과가 STAGE_2_ADVANCING일 것. (= 종가가 200일 이동평균선 위 + 200MA 우상향)

단기 과매도 트리거: IndicatorService.get_rsi(code, period=2) 의 직전 일봉 RSI 값이 10 이하로 떨어질 것.

유동성 가드: Pool A 진입 시점에서 OSBWatchlistItem.avg_trading_value_5d (5일 평균 거래대금) 기준치 이상이 보장됨을 활용. (별도 필터 불필요)

2. 🎯 페이즈 2: 매수 진입 (Trigger)
바닥에서 줍지 않습니다. 종가가 확정되기 직전 RSI(2) ≤ 10 상태가 유지될 때만 종가 베팅으로 진입합니다.

진입 시각: 15:10 이후 종가 베팅 진입. (장중 노이즈 회피, 일봉 RSI 확정성 확보)

지수 마켓 타이밍 (안전장치): OneilUniverseService.is_market_timing_ok("KOSPI"|"KOSDAQ") 가 🟢이면 정상 비중 진입.

🔴(지수 20MA 우하향)이더라도 개별 종목의 200MA 강세가 유지되면 비중의 50%만 진입 허용. (개별 강도 우선)

포지션 사이징: Fixed-fractional 방식, 1회 진입당 자본의 0.5R (R = 손절폭 × 수량) 기본. (수치는 운영 합의 후 확정)

중복 진입 금지: 동일 종목이 보유 중이면 추가 매수 금지. 청산 후 최소 2거래일 쿨다운 적용. (추후 합의)

3. ✂️ 페이즈 3: 청산 전략 (Exit)
래리 코너스 RSI(2)의 본질은 '평균 회귀'. 짧게 잡고 빠르게 끊어냅니다. (평균 보유 ~2.5일)

빠른 복귀 익절: 주가가 5일 이동평균선(5MA)에 터치하는 순간 전량 시장가 익절. (IndicatorService.get_moving_average(period=5) 일봉 기준)

하드 스탑 (가격): 진입가 대비 -5% 도달 시 즉시 전량 손절.

하드 스탑 (추세 붕괴): 종가 기준 200일 이동평균선 하향 이탈 시 즉시 전량 손절. (Stage 2 가정 무너짐)

EOD 점검: 매 장마감 후 보유 포지션 전체에 대해 위 3개 조건을 일괄 평가하여 다음 장 시초가/장중 처리 큐에 넣음.

4. ⚙️ 파라미터 요약
| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `rsi_period` | 2 | RSI 기간 (코너스 원안 고정) |
| `rsi_threshold` | 10 | 진입 허용 RSI 상한 |
| `entry_cutoff_time` | 15:10 | 종가 베팅 진입 시작 시각 |
| `risk_off_position_ratio` | 0.5 | 지수 마켓 타이밍 🔴일 때 비중 |
| `take_profit_ma_period` | 5 | 익절 기준 이동평균 (5MA 터치) |
| `hard_stop_pct` | -5.0% | 가격 기준 손절폭 |
| `trend_break_ma_period` | 200 | 추세 붕괴 손절 기준 (200MA) |
| `reentry_cooldown_days` | 2 | 청산 후 동일 종목 재진입 차단 일수 |

5. 🔒 전략 불변 원칙 (Invariants)
- Stage 2 전제: 200MA 위 + 우상향 종목만 진입. 약세장 진입 금지.
- 일봉 트리거: RSI(2)는 일봉 종가로만 평가. 분봉 RSI는 사용하지 않음.
- 1종목 1회 진입: 보유 중인 종목은 평단가 평균화 목적의 추가 매수 금지.
- 시장 vs 개별 우선순위: 시장 🔴이면 비중을 줄일 뿐, 개별 종목의 200MA 강세까지 무시하지는 않음.
- LiveStrategy 인터페이스 준수: scan() / check_exits(holdings) 두 메서드만 외부에 노출.

6. 🧩 의존 서비스 / 인터페이스
- interfaces/live_strategy.py — LiveStrategy 베이스 (scan, check_exits)
- services/oneil_universe_service.py — get_watchlist (Pool A), is_market_timing_ok
- services/indicator_service.py — get_rsi(period=2), get_moving_average(period=5|200)
- services/minervini_stage_service.py — classify_stage (STAGE_2_ADVANCING 확인)
- strategies/oneil_common_types.py — OSBWatchlistItem (ma_20d, w52_hgpr, avg_trading_value_5d 등)

7. 🧪 백테스트 / 검증 메모
- 데이터: 일봉 OHLCV (Pool A 종목 한정).
- 측정: 평균 보유일수(목표 ~2.5일), 승률, 평균 손익비, RSI(2) ≤ 10 발생 빈도.
- 시나리오: 강세장(2020~2021), 박스권(2022 상반기), 약세장(2022 하반기) 구간별 성과 분리.
- mock 백테스트 케이스 1개를 먼저 작성하여 시그널 트리거/청산 로직을 단위 검증한 뒤 실데이터로 확장.

8. ⚠️ 알려진 갭 / 향후 보완
- 분봉 RSI 미노출: IndicatorService는 일봉 RSI만 제공. 본 전략은 일봉 종가 기반이므로 영향 없음.
- 200MA 기울기 raw 미노출: MinerviniStageService는 stage enum만 반환. STAGE_2_ADVANCING 비교로 갈음.
- RSI(2) ≤ 10 발생 빈도가 낮아 슬립 상태가 길 수 있음. 다른 전략과 병행 운영 권장.
- 포지션 사이징/쿨다운 수치는 운영 합의 전 임시값. 실 자금 투입 전 합의 후 확정 필요.
