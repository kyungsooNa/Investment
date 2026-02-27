# core/korea_invest_url_provider.py
from __future__ import annotations
from typing import Mapping, Iterable, Optional, Callable, Union
from enum import Enum
from urllib.parse import urljoin
from config.config_loader import load_configs, load_config, KIS_CONFIG_PATH
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv


class KoreaInvestUrlProvider:
    """
    kis_config.yaml(paths) + base_url을 바탕으로 엔드포인트 절대 URL을 만들어주는 경량 Provider.
    - 기본적으로 kis_config.yaml 만 읽어 경로 테이블(paths)을 구성
    - base_url은 생성자 인자로 주입 (권장)하거나, load_configs() 병합 결과에 존재하면 자동 사용
    """
    def __init__(self, get_base_url: Callable[[], str], paths: Mapping[str, str]) -> None:
        self._get_base_url = get_base_url
        self._paths = dict(paths or {})

    @classmethod
    def from_env_and_kis_config(cls, env: KoreaInvestApiEnv, kis_config_override: Optional[dict] = None) -> "KoreaInvestUrlProvider":
        kis_conf = kis_config_override if kis_config_override is not None else load_config(KIS_CONFIG_PATH)
        paths = kis_conf.get("paths")
        if not isinstance(paths, dict) or not paths:
            raise ValueError("kis_config.yaml의 paths가 없거나 비었습니다.")
        # base_url은 사용하지 않음(동적으로 env에서 받음)
        return cls(get_base_url=env.get_base_url, paths=paths)


    # ---------- 조회 ----------
    def has(self, key: str) -> bool:
        return key in self._paths

    def keys(self) -> Iterable[str]:
        return self._paths.keys()

    def path(self, key: str) -> str:
        if key not in self._paths:
            raise KeyError(f"kis_config.paths에 '{key}'가 없습니다.")
        return self._paths[key]

    def url(self, key_or_path: Union[str, Enum]) -> str:
        base = (self._get_base_url() or "").rstrip("/")
        if not base:
            raise ValueError("env.get_base_url()이 빈 값입니다. env.set_trading_mode(...)로 활성화했는지 확인하세요.")
        # Enum이면 .value 사용
        key_str = key_or_path.value if isinstance(key_or_path, Enum) else str(key_or_path)
        rel = self.path(key_str) if self.has(key_str) else key_str
        return urljoin(base + "/", rel.lstrip("/"))
