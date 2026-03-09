# Codex Mobile Console 部署教程（VPN + IP 直连）

本项目用于把服务器上的 `codex CLI` 以手机友好方式开放出来，核心入口：

- `/`：手机主工作台（会话、任务、变更、文件、运行）
- `/term/`：`ttyd` 终端兜底
- `/ide/`：`code-server` 备用入口

> 推荐场景：手机先连实验室 VPN，再访问服务器 VPN 内网 IP。

---

## 1. 架构说明

访问链路：

`手机浏览器 -> VPN -> 服务器IP:80 -> webterm-nginx(docker) -> /api /ttyd /ide /`

内部服务（不直接对外暴露）：

- `ttyd`：Unix Socket（`~/.local/run/ttyd/ttyd.sock`）
- `code-server`：Unix Socket（`~/.local/run/code-server/code-server.sock`）
- `codex-bridge(FastAPI)`：Unix Socket（`~/.local/run/codex-bridge/codex-bridge.sock`）

---

## 2. 先决条件

部署机需要以下命令可用：

- `docker`
- `tmux`
- `python3`
- `openssl`

并且已安装：

- `ttyd`（可在 PATH，或通过 `TTYD_BIN` 指定）
- `code-server`（可在 PATH，或通过 `CODE_SERVER_BIN` 指定）

说明：`scripts/deploy_mobile_console.sh` 负责配置和启动，不会自动安装 `ttyd/code-server`。

---

## 3. 需要配置的变量（完整）

下面是部署脚本支持的全部关键变量。

| 变量名 | 是否必须 | 默认值 | 作用 |
|---|---|---|---|
| `WORKSPACE_ROOT` | 否 | 当前仓库根目录（自动推断） | 本项目代码根目录 |
| `PROJECTS_ROOT` | 否 | `$HOME/codex` | 移动端可管理的项目根目录 |
| `PROJECT_DIR` | 否 | `$PROJECTS_ROOT` | 首次默认打开目录 |
| `MOBILE_USER` | 建议设置 | `chd` | Web Basic Auth 用户名 |
| `MOBILE_PASS` | 建议设置 | `chd` | Web Basic Auth 密码 |
| `LISTEN_PORT` | 否 | `80` | nginx 容器对外端口 |
| `WEBTERM_CONTAINER_NAME` | 否 | `webterm-nginx` | nginx 容器名（便于并行测试） |
| `TTYD_BIN` | 否 | `~/.local/bin/ttyd`（不存在则尝试 PATH） | `ttyd` 可执行文件路径 |
| `CODE_SERVER_BIN` | 否 | `~/.local/bin/code-server`（不存在则尝试 PATH） | `code-server` 可执行文件路径 |
| `TTYD_LD_LIBRARY_PATH` | 否 | 自动探测 `~/miniconda3/lib` | 仅用于启动 `ttyd` 的动态库路径（如 `libev.so.4`） |
| `ENABLE_FIREWALL` | 否 | `0` | 是否尝试自动配置防火墙 |
| `VPN_CIDR` | `ENABLE_FIREWALL=1` 时必须 | 空 | 允许访问网段（如 `10.26.43.0/24`） |
| `GITHUB_OWNER` | 可选 | 空 | 新建项目时创建 GitHub 仓库所属 owner |
| `GITHUB_TOKEN` | 可选 | 空 | GitHub PAT（`repo` 权限） |
| `GITHUB_DEFAULT_PRIVATE` | 否 | `1` | GitHub 新仓库默认私有（`1/0`） |

---

## 4. 标准部署（生产入口）

在服务器上执行：

```bash
cd <your-repo-path>
MOBILE_USER='your_user' \
MOBILE_PASS='your_strong_password' \
bash scripts/deploy_mobile_console.sh
```

部署完成后访问：

- `http://<SERVER_VPN_IP>/`

登录凭据就是 `MOBILE_USER / MOBILE_PASS`。

---

## 5. 可选：启用“新项目自动建 GitHub 仓库”

如果希望在手机端点“新建项目”时自动创建 GitHub repo：

```bash
cd <your-repo-path>
MOBILE_USER='your_user' \
MOBILE_PASS='your_strong_password' \
GITHUB_OWNER='<github_owner>' \
GITHUB_TOKEN='<github_pat_with_repo_scope>' \
GITHUB_DEFAULT_PRIVATE=1 \
bash scripts/deploy_mobile_console.sh
```

注意：

- `GITHUB_TOKEN` 不要写进仓库文件，建议只在命令行或 CI secret 中注入。
- 该 token 会写入本机运行环境文件（`~/.local/share/codex-mobile/codex-bridge.env`），权限为 `600`。

---

## 6. 可选：同机“无侵入测试部署”

如果你要先验证脚本可移植性，不影响现有 `:80` 入口，可用独立端口和容器名：

```bash
cd <your-repo-path>
LISTEN_PORT=18080 \
WEBTERM_CONTAINER_NAME=webterm-nginx-test \
MOBILE_USER='test' \
MOBILE_PASS='test_pass' \
bash scripts/deploy_mobile_console.sh
```

访问：

- `http://<SERVER_VPN_IP>:18080/`

---

## 7. 验收清单（建议逐项）

1. 打开 `http://<SERVER_VPN_IP>/`，出现 Basic Auth。
2. 输入账号密码后能进入手机工作台首页。
3. 打开 `/term/` 能进入终端。
4. 在终端执行 `codex --version` 有输出。
5. 断网/切后台后重新打开，能恢复到同一个 tmux 会话。
6. `/api/healthz` 返回 `{"status":"ok"}`。

本机快速检测命令：

```bash
curl -u '<user>:<pass>' http://127.0.0.1/ -I
curl -u '<user>:<pass>' http://127.0.0.1/api/healthz
systemctl --user is-active ttyd-codex.service
systemctl --user is-active code-server-codex.service
systemctl --user is-active codex-bridge.service
docker ps | rg webterm-nginx
```

---

## 8. 日常运维

查看服务：

```bash
systemctl --user status ttyd-codex.service
systemctl --user status code-server-codex.service
systemctl --user status codex-bridge.service
docker ps | rg webterm-nginx
```

重启服务：

```bash
systemctl --user restart ttyd-codex.service
systemctl --user restart code-server-codex.service
systemctl --user restart codex-bridge.service
docker restart webterm-nginx
```

看日志：

```bash
journalctl --user -u ttyd-codex.service -f
journalctl --user -u code-server-codex.service -f
journalctl --user -u codex-bridge.service -f
docker logs -f webterm-nginx
```

---

## 9. 常见问题排障

### 9.1 页面能打开但 API 502

通常是 `codex-bridge` socket 不可用或服务在重启中。

```bash
systemctl --user kill codex-bridge.service
systemctl --user start codex-bridge.service
curl -u '<user>:<pass>' http://127.0.0.1/api/healthz
```

### 9.2 页面显示 `Press to reconnect`

优先检查：

- VPN 是否稳定
- `/ttyd/` WebSocket 是否被代理头破坏
- `docker logs webterm-nginx` 是否有 upstream 连接错误

### 9.3 手机登录慢/延迟高

常见是网络链路质量问题（VPN/中继）而非服务器 CPU。

建议：

- 先在同网段笔记本测试同 URL
- 对比 `term` 与首页流畅度定位瓶颈

### 9.4 会话列表里看不到你预期的会话

- Mac 端建议在 `tmux` 中跑 codex，便于跨端恢复。
- VSCode 插件会话通过 `~/.codex/sessions/*.jsonl` 被发现并可 resume。

---

## 10. 安全建议（强烈建议）

1. 仅允许 VPN 网段访问 80/443（防火墙白名单）。
2. `MOBILE_PASS` 使用强随机密码，不要长期用默认值。
3. `GITHUB_TOKEN` 使用最小权限，定期轮换。
4. 不要把 token、密码提交到 Git 仓库。

---

## 11. 回滚方式

只回滚 web 入口：

```bash
docker rm -f webterm-nginx
```

停后端：

```bash
systemctl --user stop codex-bridge.service
systemctl --user stop ttyd-codex.service
systemctl --user stop code-server-codex.service
```

恢复到旧配置：重新执行你之前稳定版本的部署脚本即可。

---

## 12. 关键文件

- `scripts/deploy_mobile_console.sh`：一键部署脚本
- `mobile_console/nginx/mobile-console.conf`：Nginx 路由模板
- `mobile_console/systemd/codex-bridge.service`：`codex-bridge` 服务模板
- `mobile_console/frontend/`：手机前端
- `mobile_console/backend/app/`：后端 API 与会话管理
