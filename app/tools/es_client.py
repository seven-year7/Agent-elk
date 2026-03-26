"""
 * @Module: app/tools/es_client
 * @Description: Elasticsearch 客户端封装、索引幂等初始化与单条日志写入
 * @Interface: ESNervousSystem, es_node
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from elasticsearch import Elasticsearch

from app.schema.log_schema import INDEX_MAPPING, LOG_INDEX_NAME
from app.tools.config import ES_DEFAULT_URL

logger = logging.getLogger(__name__)

# @Step: 1 - 从仓库根目录加载 .env，避免工作目录变化导致读不到配置
# @Security: 禁止在日志中输出 ES_API_KEY、ES_PASSWORD
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def _env_str(name: str) -> str | None:
    # @Step: 1b - 读取可选字符串环境变量，空串视为未配置
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _env_bool(name: str, default: bool) -> bool:
    # @Step: 1c - 解析布尔环境变量；无法识别时保留 default
    raw = os.getenv(name)
    if raw is None or not isinstance(raw, str):
        return default
    token = raw.strip().lower()
    if token in ("0", "false", "no", "off"):
        return False
    if token in ("1", "true", "yes", "on"):
        return True
    return default


class ESNervousSystem:
    """ES 连接与索引生命周期的薄封装，供工具层复用。"""

    def __init__(self) -> None:
        # @Step: 2 - 按环境变量构造官方 Python 客户端（与 requirements 中 elasticsearch 8.x 对齐）
        # @Agent_Logic: 优先 API Key；未配置时再使用 Basic（用户名+密码），与 Kibana 网页登录同一套 elastic 用户体系
        url = os.getenv("ES_URL", ES_DEFAULT_URL)
        api_key = _env_str("ES_API_KEY")
        username = _env_str("ES_USERNAME")
        password = _env_str("ES_PASSWORD")

        client_kwargs: dict = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        elif username is not None and password is not None:
            client_kwargs["basic_auth"] = (username, password)

        # @Step: 2b - HTTPS + 自签名/内网 CA：可指定 CA 文件，或仅在开发环境关闭校验（不推荐生产）
        # @Security: verify_certs=false 会易受中间人攻击；生产请使用 ES_CA_CERTS 指向集群根 CA
        verify_certs = _env_bool("ES_VERIFY_CERTS", True)
        ca_certs_path = _env_str("ES_CA_CERTS")
        if ca_certs_path:
            ca_file = Path(ca_certs_path)
            if ca_file.is_file():
                client_kwargs["ca_certs"] = str(ca_file.resolve())
            else:
                logger.warning("[WARN][ESClient]: ES_CA_CERTS 文件不存在，已忽略: %s", ca_certs_path)
        if not verify_certs:
            client_kwargs["verify_certs"] = False
            logger.warning("[WARN][ESClient]: TLS 证书校验已关闭（ES_VERIFY_CERTS=false），仅限可信网络/开发环境")

        self.client = Elasticsearch(url, **client_kwargs)

        logger.info(
            "[INFO][ESClient]: Elasticsearch 客户端已初始化（url=%s, auth=api_key=%s, basic=%s, verify_certs=%s）",
            url,
            bool(api_key),
            bool(username and password and not api_key),
            client_kwargs.get("verify_certs", True),
        )

    def init_index(self) -> str:
        """若索引不存在则创建；已存在则跳过。"""
        # @Agent_Logic: elasticsearch-py 8.x 使用 mappings= / settings=，不再使用 body= 整体传入
        if self.client.indices.exists(index=LOG_INDEX_NAME):
            logger.info("[INFO][ESClient]: 索引已存在，跳过创建: %s", LOG_INDEX_NAME)
            return f"ℹ️ 索引 {LOG_INDEX_NAME} 已存在，跳过创建。"

        self.client.indices.create(index=LOG_INDEX_NAME, mappings=INDEX_MAPPING["mappings"])
        logger.info("[INFO][ESClient]: 索引创建成功: %s", LOG_INDEX_NAME)
        return f"✅ 索引创建成功: {LOG_INDEX_NAME}"

    def push_log(self, doc: object) -> object:
        """向当前日志索引写入单条文档。"""
        # @Step: 3 - index API 使用 document= 以匹配 elasticsearch-py 8.x
        result = self.client.index(index=LOG_INDEX_NAME, document=doc)
        logger.debug("[DEBUG][ESClient]: push_log 已完成（索引=%s）", LOG_INDEX_NAME)
        return result


# @Agent_Logic: 模块级单例便于 Tool 直接引用；导入本模块即完成客户端构造（首次请求前不主动 ping）
es_node = ESNervousSystem()
