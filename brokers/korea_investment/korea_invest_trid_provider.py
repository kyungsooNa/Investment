from __future__ import annotations
from typing import Dict, Optional, Union
from enum import Enum

from config.config_loader import load_configs, load_config, TR_IDS_CONFIG_PATH
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from brokers.korea_investment.korea_invest_trid_keys import TrIdLeaf, TrId

_DOMAINS = ("quotations", "account", "trading")

class KoreaInvestTrIdProvider:
    """
    - tr_ids_config.yaml에서 TR-ID 테이블을 읽어와 키 기반으로 안전하게 조회
    - 모드(모의/실전)에 따라 자동으로 paper/real leaf 선택(논리 키 TrId 사용 시)
    - 문자열/Enum 모두 지원
    """

    def __init__(self, env: KoreaInvestApiEnv, tr_ids: Dict) -> None:
        self._env = env
        if not isinstance(tr_ids, dict) or not tr_ids:
            raise ValueError("tr_ids 설정이 비어 있거나 올바르지 않습니다.")
        self._tr_ids = tr_ids  # shape: {'quotations': {...}, 'account': {...}, 'trading': {...}}

    # ---------- 팩토리 ----------
    @classmethod
    def from_config_loader(cls, env: KoreaInvestApiEnv) -> "KoreaInvestTrIdProvider":
        merged = load_configs()
        tr_ids = merged.get("tr_ids")
        if not tr_ids:
            # 병합에 없다면 원본 파일 직접 로드
            tr_ids = load_config(TR_IDS_CONFIG_PATH).get("tr_ids")
        return cls(env, tr_ids)

    # ---------- 내부 유틸 ----------
    def _get_leaf_value(self, leaf_key: str) -> str:
        """
        leaf_key가 어느 도메인(account/quotations/trading)에 있든 값을 찾아 반환.
        """
        for domain in _DOMAINS:
            dom = self._tr_ids.get(domain, {})
            if leaf_key in dom:
                return dom[leaf_key]
        raise KeyError(f"tr_ids에 '{leaf_key}'를 찾을 수 없습니다.")

    # ---------- 공개 API ----------
    def get_by_leaf(self, key: Union[str, TrIdLeaf]) -> str:
        """leaf 키로 직접 조회 (모드 무시)"""
        leaf = key.value if isinstance(key, Enum) else str(key)
        return self._get_leaf_value(leaf)

    def get(self, key: Union[str, TrId, TrIdLeaf], **kwargs) -> str:
        """
        - TrId(논리키): 모드에 따라 paper/real leaf 자동 선택
        - TrIdLeaf/str(leaf키): 그대로 반환
        """
        # 논리 키 처리
        if isinstance(key, TrId):
            is_paper = bool(self._env.is_paper_trading)
            if key is TrId.INQUIRE_BALANCE:
                leaf = TrIdLeaf.INQUIRE_BALANCE_PAPER if is_paper else TrIdLeaf.INQUIRE_BALANCE_REAL
                return self.get_by_leaf(leaf)
            if key is TrId.ORDER_CASH_BUY:
                leaf = TrIdLeaf.ORDER_CASH_BUY_PAPER if is_paper else TrIdLeaf.ORDER_CASH_BUY_REAL
                return self.get_by_leaf(leaf)
            if key is TrId.ORDER_CASH_SELL:
                leaf = TrIdLeaf.ORDER_CASH_SELL_PAPER if is_paper else TrIdLeaf.ORDER_CASH_SELL_REAL
                return self.get_by_leaf(leaf)
            raise KeyError(f"지원하지 않는 논리 키: {key}")

        # leaf 키 그대로
        return self.get_by_leaf(key)

    # 편의 함수(읽기 쉬운 API)
    def quotations(self, leaf: TrIdLeaf) -> str:
        return self.get_by_leaf(leaf)

    def account_inquire_balance(self) -> str:
        return self.get(TrId.INQUIRE_BALANCE)

    def trading_order_cash(self, is_buy: bool) -> str:
        return self.get(TrId.ORDER_CASH_BUY if is_buy else TrId.ORDER_CASH_SELL)

    def daily_itemchartprice(self) -> str:
        leaf = TrIdLeaf.DAILY_ITEMCHARTPRICE
        return self.get_by_leaf(leaf)

    def time_itemchartprice(self) -> str:
        leaf = TrIdLeaf.TIME_ITEMCHARTPRICE
        return self.get_by_leaf(leaf)

    def time_daily_itemchartprice(self) -> str:
        leaf = TrIdLeaf.TIME_DAILY_ITEMCHARTPRICE
        return self.get_by_leaf(leaf)
