"""
 * @Module: test/test_es_tls_certificate
 * @Description: Elasticsearch TLS/证书专项排查脚本（握手层 + SDK 层）
 * @Interface: python test/test_es_tls_certificate.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from elasticsearch import Elasticsearch


# @Step: 1 - 加载项目根目录 .env，保证和主程序读取一致
# @Agent_Logic: 证书问题排查要尽量复用生产同一套环境变量，减少“脚本能跑/程序报错”的偏差
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_PROJECT_ROOT / ".env")


def _env_str(name: str) -> str | None:
    val = os.getenv(name)
    if not isinstance(val, str):
        return None
    text = val.strip()
    return text or None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not isinstance(raw, str):
        return default
    token = raw.strip().lower()
    if token in ("1", "true", "yes", "on"):
        return True
    if token in ("0", "false", "no", "off"):
        return False
    return default


def _build_auth_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = _env_str("ES_API_KEY")
    if api_key:
        headers["Authorization"] = f"ApiKey {api_key}"
    return headers


def _build_es_client_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    api_key = _env_str("ES_API_KEY")
    username = _env_str("ES_USERNAME")
    password = _env_str("ES_PASSWORD")
    verify_certs = _env_bool("ES_VERIFY_CERTS", True)
    ca_certs = _env_str("ES_CA_CERTS")

    if api_key:
        kwargs["api_key"] = api_key
    elif username and password:
        kwargs["basic_auth"] = (username, password)

    if ca_certs:
        ca_file = Path(ca_certs)
        if ca_file.is_file():
            kwargs["ca_certs"] = str(ca_file.resolve())
        else:
            print(f"[WARN][TLS-Test]: ES_CA_CERTS 文件不存在，将忽略: {ca_certs}")
    if not verify_certs:
        kwargs["verify_certs"] = False
    return kwargs


def _requests_tls_check(es_url: str, verify_option: bool | str, headers: dict[str, str]) -> bool:
    print("[INFO][TLS-Test]: Step1 requests 握手检查开始")
    try:
        response = requests.get(es_url, headers=headers, timeout=8, verify=verify_option)
        print(f"[OK][TLS-Test]: requests 握手成功，HTTP={response.status_code}")
        return True
    except requests.exceptions.SSLError as exc:
        print(f"[ERROR][TLS-Test]: requests TLS 校验失败: {exc}")
        return False
    except Exception as exc:
        print(f"[ERROR][TLS-Test]: requests 连接失败: {exc}")
        return False


def _sdk_tls_check(es_url: str, kwargs: dict[str, Any]) -> bool:
    print("[INFO][TLS-Test]: Step2 elasticsearch 客户端检查开始")
    try:
        client = Elasticsearch(es_url, request_timeout=8, **kwargs)
        info = client.info()
        cluster_name = (info or {}).get("cluster_name")
        version = ((info or {}).get("version") or {}).get("number")
        print(f"[OK][TLS-Test]: SDK 连接成功，cluster={cluster_name}, version={version}")
        return True
    except Exception as exc:
        print(f"[ERROR][TLS-Test]: SDK 连接失败: {exc}")
        return False


def main() -> int:
    es_url = _env_str("ES_URL") or _env_str("MCP_ES_URL") or "http://localhost:9200"
    verify_certs = _env_bool("ES_VERIFY_CERTS", True)
    ca_certs = _env_str("ES_CA_CERTS")

    # @Security: 输出诊断信息时不打印 API Key/密码，仅展示开关状态
    print(f"[INFO][TLS-Test]: ES_URL={es_url}")
    print(f"[INFO][TLS-Test]: ES_VERIFY_CERTS={verify_certs}")
    print(f"[INFO][TLS-Test]: ES_CA_CERTS={'set' if ca_certs else 'unset'}")
    print(f"[INFO][TLS-Test]: ES_API_KEY={'set' if _env_str('ES_API_KEY') else 'unset'}")
    print(
        "[INFO][TLS-Test]: ES_BASIC_AUTH="
        f"{'set' if (_env_str('ES_USERNAME') and _env_str('ES_PASSWORD')) else 'unset'}"
    )

    verify_option: bool | str = True
    if not verify_certs:
        verify_option = False
    elif ca_certs and Path(ca_certs).is_file():
        verify_option = str(Path(ca_certs).resolve())

    ok_handshake = _requests_tls_check(es_url, verify_option, _build_auth_headers())
    ok_sdk = _sdk_tls_check(es_url, _build_es_client_kwargs())

    if ok_handshake and ok_sdk:
        print("[OK][TLS-Test]: 证书链与 SDK 均通过")
        return 0

    print("[ERROR][TLS-Test]: 检测未通过，请根据上方失败阶段定位（握手层或 SDK 层）")
    return 1


if __name__ == "__main__":
    sys.exit(main())

