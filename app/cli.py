"""运维 CLI。

用法示例：
    python -m app.cli daemon           # 启动守护进程
    python -m app.cli status           # 运行状态 + 数据概览
    python -m app.cli config           # 查看配置（密钥打码）
    python -m app.cli backup-now       # 立即备份
    python -m app.cli push-test        # 向用户发测试消息
    python -m app.cli list             # 列卡片
    python -m app.cli stats            # 统计
"""
from __future__ import annotations

import argparse
import logging
import sys

from . import backup, db, due
from .config import masked, settings
from .feishu.client import FeishuClient
from .feishu.handlers import build_stats, current_user, push_review


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _connect() -> tuple:
    s = settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(s.db_path)
    db.init_db(conn)
    return s, conn


# ---------- 子命令 ----------

def cmd_daemon(_args) -> int:
    import asyncio

    from .daemon import run

    asyncio.run(run())
    return 0


def cmd_status(_args) -> int:
    s, conn = _connect()
    ok, missing = s.is_complete
    print(f"配置完整性: {'[OK] 完整' if ok else '[X] 缺失: ' + ', '.join(missing)}")
    print(f"时区: {s.timezone}  推送时段: {s.push_slots}  每日上限: {s.daily_cap}")
    print(f"备份: {s.backup_time}  文档ID: {s.backup_doc_id or '(未创建)'}")
    print(f"用户 open_id: {current_user(conn) or '(未记录)'}")
    print(f"数据文件: {s.db_path}")
    stats = build_stats(conn)
    print(f"\n活跃卡片: {stats['total']}  今天到期: {stats['due']}")
    print(f"熟练(>=21天): {stats['mastered']}  7天复习: {stats['reviewed_7d']} 次  草稿: {stats['drafts']}")
    return 0


def cmd_config(args) -> int:
    _s, conn = _connect()
    if args.set:
        key, _, value = args.set.partition("=")
        if not key or not value:
            print("用法: config --set KEY=VALUE")
            return 1
        db.config_set(conn, key.strip(), value.strip())
        print(f"已设置 {key.strip()}={value.strip()}")
        return 0
    s = settings()
    print("--- 环境变量 ---")
    for k, v in [
        ("DEEPSEEK_API_KEY", masked(s.deepseek_api_key)),
        ("FEISHU_APP_ID", masked(s.feishu_app_id)),
        ("FEISHU_APP_SECRET", masked(s.feishu_app_secret)),
        ("FEISHU_USER_OPEN_ID", s.feishu_user_open_id or "(未设置)"),
        ("PUSH_SLOTS", ", ".join(s.push_slots)),
        ("DAILY_CAP", str(s.daily_cap)),
        ("TIMEZONE", s.timezone),
        ("BACKUP_TIME", s.backup_time),
    ]:
        print(f"{k}={v}")
    print("--- 运行时配置 (SQLite) ---")
    for row in conn.execute("SELECT key, value FROM app_config ORDER BY key").fetchall():
        v = row["value"]
        if "secret" in row["key"].lower() or "open_id" in row["key"].lower():
            v = masked(v)
        print(f"{row['key']}={v}")
    return 0


def cmd_backup_now(_args) -> int:
    s, conn = _connect()
    feishu = FeishuClient()
    open_id = current_user(conn)
    if not open_id:
        print("未记录用户 open_id，无法推送备份。先让用户给机器人发一条消息。")
        return 1
    result = backup.run_backup(conn, feishu, open_id)
    print(f"本地备份: {result['local']}")
    print(f"飞书备份: {'[OK] 已上传' if result['feishu'] else '[X] 失败（看日志）'}")
    return 0


def cmd_push_test(_args) -> int:
    s, conn = _connect()
    feishu = FeishuClient()
    open_id = current_user(conn)
    if not open_id:
        print("未记录用户 open_id，无法推送。先让用户给机器人发一条消息。")
        return 1
    ok = feishu.echo(open_id)
    print("[OK] 已发送测试消息" if ok else "[X] 发送失败（看日志）")
    return 0 if ok else 1


def cmd_list(args) -> int:
    _s, conn = _connect()
    rows = conn.execute(
        """
        SELECT id, status, mode, question, due_at FROM cards
        WHERE status IN ('active','draft')
        ORDER BY status, id
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()
    if not rows:
        print("暂无卡片。")
        return 0
    for r in rows:
        tag = {"active": "[A]", "draft": "[D]"}[r["status"]]
        mode = "仅重读" if r["mode"] == "readonly" else ""
        print(f"{tag} #{r['id']} {mode} {r['question'][:40]}  到期 {r['due_at'] or '-'}")
    return 0


def cmd_stats(_args) -> int:
    _s, conn = _connect()
    st = build_stats(conn)
    for k, v in st.items():
        print(f"{k}: {v}")
    return 0


# ---------- 入口 ----------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="remenberall", description="飞书驱动的间隔重复记忆系统")
    parser.add_argument("-v", "--verbose", action="store_true", help="调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("daemon", help="启动守护进程")
    sub.add_parser("status", help="运行状态")
    p = sub.add_parser("config", help="查看/修改配置")
    p.add_argument("--set", help="KEY=VALUE")
    sub.add_parser("backup-now", help="立即备份")
    sub.add_parser("push-test", help="发送测试消息")
    p = sub.add_parser("list", help="列卡片")
    p.add_argument("--limit", type=int, default=50)
    sub.add_parser("stats", help="统计")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    handlers = {
        "daemon": cmd_daemon,
        "status": cmd_status,
        "config": cmd_config,
        "backup-now": cmd_backup_now,
        "push-test": cmd_push_test,
        "list": cmd_list,
        "stats": cmd_stats,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
