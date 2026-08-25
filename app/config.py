"""配置加载。

密钥（DeepSeek / 飞书）只从环境变量读取，绝不写进代码或配置文件。
运行时配置（推送时段、备份文档 ID 等）存储在 SQLite 的 app_config 表，
可通过 `rmem config get/set` 查看与修改。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（app/ 的上一级）
ROOT_DIR = Path(__file__).resolve().parent.parent


def _resolve_data_dir(raw: str) -> Path:
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT_DIR / p
    return p.resolve()


@dataclass
class Settings:
    """环境变量配置。密钥字段以 *_KEY 命名，禁止在 __str__/日志中输出。"""

    # DeepSeek
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    # 飞书
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_user_open_id: str = ""

    # 提醒节奏
    push_slots: list[str] = field(default_factory=lambda: ["07:00", "18:00"])
    daily_cap: int = 15
    timezone: str = "Asia/Shanghai"

    # 备份
    backup_time: str = "03:10"
    backup_doc_id: str = ""

    # 数据
    data_dir: Path = field(default_factory=lambda: _resolve_data_dir("./data"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "remenberall.sqlite3"

    @property
    def is_complete(self) -> tuple[bool, list[str]]:
        """检查必填项是否齐全，返回 (是否齐全, 缺失项列表)。"""
        missing = []
        if not self.deepseek_api_key:
            missing.append("DEEPSEEK_API_KEY")
        if not self.feishu_app_id:
            missing.append("FEISHU_APP_ID")
        if not self.feishu_app_secret:
            missing.append("FEISHU_APP_SECRET")
        return (not missing, missing)


def load_settings() -> Settings:
    """从环境变量加载配置。.env 文件优先（权限应为 0600）。"""
    load_dotenv(ROOT_DIR / ".env")
    load_dotenv(ROOT_DIR / ".env.local")

    def _csv(v: str) -> list[str]:
        return [s.strip() for s in v.split(",") if s.strip()]

    return Settings(
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        feishu_app_id=os.environ.get("FEISHU_APP_ID", ""),
        feishu_app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
        feishu_user_open_id=os.environ.get("FEISHU_USER_OPEN_ID", ""),
        push_slots=_csv(os.environ.get("PUSH_SLOTS", "07:00,18:00")),
        daily_cap=int(os.environ.get("DAILY_CAP", "15")),
        timezone=os.environ.get("TIMEZONE", "Asia/Shanghai"),
        backup_time=os.environ.get("BACKUP_TIME", "03:10"),
        backup_doc_id=os.environ.get("BACKUP_DOC_ID", ""),
        data_dir=_resolve_data_dir(os.environ.get("DATA_DIR", "./data")),
    )


def masked(value: str) -> str:
    """密钥打码显示：sk-abc… 只留前 6 后 4。"""
    if not value:
        return "(未配置)"
    if len(value) <= 10:
        return value[0] + "***"
    return f"{value[:6]}…{value[-4:]}"


# 全局单例
_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
