# core/config_loader.py
import yaml
import os
import json

# config.yaml 및 tr_ids_config.yaml 파일 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_CONFIG_PATH = os.path.join(BASE_DIR, 'config.yaml')
TR_IDS_CONFIG_PATH = os.path.join(BASE_DIR, 'tr_ids_config.yaml')
KIS_CONFIG_PATH = os.path.join(BASE_DIR, 'kis_config.yaml')


# def load_config(config_path):
#     """config.yaml 파일을 로드합니다."""
#     if not os.path.exists(config_path):
#         raise FileNotFoundError(f"Config file not found at: {config_path}")
#     with open(config_path, 'r', encoding='utf-8') as f:
#         return yaml.safe_load(f)
#
#

def load_configs() -> dict:
    main_config_data = load_config(MAIN_CONFIG_PATH)
    tr_ids_data = load_config(TR_IDS_CONFIG_PATH)
    kis_config_data = load_config(KIS_CONFIG_PATH)

    config_data = {}
    config_data.update(main_config_data)
    config_data.update(tr_ids_data)
    config_data.update(kis_config_data)

    return config_data


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
