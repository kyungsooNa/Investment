# brokers/korea_investment/korea_invest_header_provider.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, Iterator
from contextlib import contextmanager
import os


@dataclass
class KoreaInvestHeaderProvider:
    """
    중앙집중 헤더 관리자.
    - 기본 헤더 보유
    - 토큰/앱키/앱시크릿 자동 주입
    - TR-ID, custtype, hashkey, gt_uid 등 단건 API 실행 전/중 임시 설정(Context) 지원
    - dict를 직접 노출하지 않고, 매 호출 시 `build()`로 사본 생성
    """
    my_agent: str
    appkey: str = ""
    appsecret: str = ""
    custtype_default: str = "P"

    # 내부 상태(임시/지속) 분리
    _base: Dict[str, str] = field(default_factory=dict)
    _volatile: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        self._base = {
            "Content-Type": "application/json",
            "User-Agent": self.my_agent,
            "charset": "UTF-8",
            "Authorization": "",
            "appkey": self.appkey,
            "appsecret": self.appsecret,
            # custtype은 보통 공통이지만, 엔드포인트별 변경 가능 → volatile에 넣는 편의 메서드 제공
        }
        self._volatile = {"custtype": self.custtype_default}

    # --------- 지속 필드 설정 ---------
    def set_auth_bearer(self, access_token: str) -> None:
        self._base["Authorization"] = f"Bearer {access_token}"

    def set_app_keys(self, appkey: str, appsecret: str) -> None:
        self._base["appkey"] = appkey
        self._base["appsecret"] = appsecret

    # --------- 임시(요청별) 필드 설정 ---------
    def set_tr_id(self, tr_id: Optional[str]) -> None:
        if tr_id is None:
            self._volatile.pop("tr_id", None)
        else:
            self._volatile["tr_id"] = tr_id

    def set_custtype(self, custtype: Optional[str]) -> None:
        if custtype is None:
            self._volatile.pop("custtype", None)
        else:
            self._volatile["custtype"] = custtype

    def set_hashkey(self, hashkey: Optional[str]) -> None:
        if hashkey is None:
            self._volatile.pop("hashkey", None)
        else:
            self._volatile["hashkey"] = hashkey

    def set_gt_uid(self, gt_uid: Optional[str] = None) -> None:
        # 주지 않을 경우 자동 생성
        if gt_uid is None:
            gt_uid = os.urandom(16).hex()
        self._volatile["gt_uid"] = gt_uid

    def clear_order_headers(self) -> None:
        # 주문계열에서 쓰는 hashkey/gt_uid 등만 정리
        for k in ("hashkey", "gt_uid"):
            self._volatile.pop(k, None)

    def sync_from_env(self, env) -> None:
        """모드가 정해진 이후 env.active_config로부터 키/모드 동기화."""
        cfg = getattr(env, "active_config", None) or {}
        self.set_app_keys(cfg.get("api_key", ""), cfg.get("api_secret_key", ""))
        self.set_custtype(cfg.get("custtype", "P"))

    # --------- 빌드/컨텍스트 ---------
    def build(self) -> Dict[str, str]:
        # 매 요청 시 사본 생성(외부 변조 방지)
        h = {**self._base, **self._volatile}
        # 값이 빈 문자열인 키는 제거(헤더 깔끔하게)
        return {k: v for k, v in h.items() if v is not None and v != ""}

    @contextmanager
    def temp(self, *, tr_id: Optional[str] = None, custtype: Optional[str] = None,
             hashkey: Optional[str] = None, gt_uid: Optional[str] = None) -> Iterator[None]:
        """요청 단위 임시 헤더 설정 컨텍스트.
        사용 예)
            with header_mgr.temp(tr_id="FHK...", custtype="P"):
                client.get(..., headers=header_mgr.build())
        컨텍스트 종료 시 원복.
        """
        backup = dict(self._volatile)
        try:
            if tr_id is not None:
                self.set_tr_id(tr_id)
            if custtype is not None:
                self.set_custtype(custtype)
            if hashkey is not None:
                self.set_hashkey(hashkey)
            if gt_uid is not None:
                self.set_gt_uid(gt_uid)
            yield
        finally:
            self._volatile = backup

    def fork(self) -> "KoreaInvestHeaderProvider":
        cloned = KoreaInvestHeaderProvider(
            my_agent=self._base.get("User-Agent", self.my_agent),
            appkey=self._base.get("appkey", ""),
            appsecret=self._base.get("appsecret", ""),
            custtype_default=self._volatile.get("custtype", self.custtype_default),
        )
        cloned._base = dict(self._base)
        cloned._volatile = dict(self._volatile)
        return cloned

# -----------------------------------------------------------------------------
# 사용 편의 팩토리
# -----------------------------------------------------------------------------
def build_header_provider_from_env(env) -> KoreaInvestHeaderProvider:
    # 생성 시엔 UA만. 키/모드는 나중에 sync_from_env로 주입
    return KoreaInvestHeaderProvider(
        my_agent=getattr(env, "my_agent", "python-client")
    )
