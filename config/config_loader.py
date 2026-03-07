# core/config_loader.py
import yaml
import os
import json
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, ValidationError, field_validator

# config.yaml 및 tr_ids_config.yaml 파일 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_CONFIG_PATH = os.path.join(BASE_DIR, 'config.yaml')
TR_IDS_CONFIG_PATH = os.path.join(BASE_DIR, 'tr_ids_config.yaml')
KIS_CONFIG_PATH = os.path.join(BASE_DIR, 'kis_config.yaml')


class WebConfig(BaseModel):
    host: str
    port: int = Field(..., ge=1, le=65535, description="웹 서버 포트 (1~65535)")

class CacheConfig(BaseModel):
    base_dir: str = ".cache"
    enabled_methods: List[str] = Field(default_factory=list)
    deserializable_classes: List[str] = Field(default_factory=list)
    memory_cache_enabled: bool = True
    file_cache_enabled: bool = True

class AppConfig(BaseModel):
    # Core API keys
    api_key: Optional[str] = None
    api_secret_key: Optional[str] = None
    base_url: Optional[str] = None
    websocket_url: Optional[str] = None
    
    # Account info
    custtype: str = "P"
    stock_account_number: Optional[str] = None
    htsid: Optional[str] = None
    
    # Flags
    is_paper_trading: bool = False
    
    # Sub-configs
    web: WebConfig
    cache: CacheConfig = Field(default_factory=CacheConfig)
    
    # Dynamic/Merged configs
    tr_ids: Dict[str, Any] = Field(default_factory=dict)
    paths: Dict[str, str] = Field(default_factory=dict)
    
    # Extra fields for anything else in config.yaml
    model_config = {"extra": "allow"}

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url은 'http://' 또는 'https://'로 시작해야 합니다.")
        return v

    def __getitem__(self, item):
        return getattr(self, item)

    def get(self, item, default=None):
        return getattr(self, item, default)

def load_configs() -> AppConfig:
    main_config_data = load_config(MAIN_CONFIG_PATH) or {}
    tr_ids_data = load_config(TR_IDS_CONFIG_PATH) or {}
    kis_config_data = load_config(KIS_CONFIG_PATH) or {}

    config_data = {}
    config_data.update(main_config_data)
    config_data.update(tr_ids_data)
    config_data.update(kis_config_data)

    try:
        return AppConfig(**config_data)
    except ValidationError as e:
        raise ValueError(f"설정 파일 유효성 검사 실패: {e}")


def load_config(file_path):
    """지정된 경로에서 YAML 또는 JSON 설정 파일을 로드합니다."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                return yaml.safe_load(f)
            except ImportError:
                f.seek(0)
                return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ValueError(f"설정 파일 형식이 올바르지 않습니다 ({file_path}): {e}")
