# core/cache/cache_config.py

import yaml
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(BASE_DIR, ".cache/")

def load_cache_config(path: str = "config/cache_config.yaml", override_base_dir: bool = True) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        if override_base_dir:
            config['cache']['base_dir'] = DEFAULT_CACHE_DIR
        return config
