# 部署到阿里云（Docker）

后端是常驻进程，建议跑在云服务器上（7×24 + 稳定公网，断网/关机不影响提醒）。
以下假设阿里云 Linux（Ubuntu 22.04 / Alibaba Cloud Linux 3），已能 SSH 登录。

## 1. 服务器准备

```bash
# 安装 Docker（阿里云镜像源）
curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
sudo systemctl enable --now docker
```

## 2. 上传项目

本地执行（或 git clone 到服务器）：

```bash
scp -r 08-remenberall root@<服务器IP>:/opt/remenberall
```

## 3. 配置密钥

```bash
cd /opt/remenberall
cp .env.example .env
vi .env                    # 填入 DEEPSEEK_API_KEY / FEISHU_APP_ID / FEISHU_APP_SECRET
chmod 600 .env             # 收紧权限，避免同机其他用户读到
```

## 4. 启动

```bash
sudo docker compose up -d --build
sudo docker compose logs -f      # 查看日志
```

之后到飞书里私聊机器人发一条消息，再：

```bash
sudo docker compose exec remenberall python -m app.cli push-test
```

## 5. 常用运维

```bash
sudo docker compose logs -f --tail=100      # 日志
sudo docker compose restart                 # 重启
sudo docker compose down                    # 停止（数据保留在 ./data）
sudo docker compose pull && sudo docker compose up -d   # 更新
```

## 6. 数据与备份

- SQLite 数据在 `./data/remenberall.sqlite3`，已通过 volume 持久化，容器重建不丢。
- 每天 `BACKUP_TIME`（默认 03:10）自动把数据追加写入固定的飞书在线文档（首次运行自动创建）。
- 本地备份文件在 `./data/backups/`，保留最近 30 份。
- 想手动备份：`docker compose exec remenberall python -m app.cli backup-now`

## 7. 安全加固建议

- 阿里云**安全组只开放必要端口**：SSH（22）+ 出站流量即可（系统只发起出站连接）。
- SSH 用**密钥登录**，关闭密码登录。
- `.env` 权限 `600`；密钥泄露时可随时在飞书/DeepSeek 后台轮换，改 `.env` 重启即生效。
- 定期检查 `sudo docker compose logs`，确认推送/备份正常。

## 8. 时区

容器内默认读取 `.env` 的 `TIMEZONE=Asia/Shanghai`。若服务器时区不同，系统内部全部按
`TIMEZONE` 计算推送时段，不受影响。
