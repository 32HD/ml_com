# Codex Mobile 重构实施状态（VPN + IP 直连版）

更新时间：2026-03-09（可用性迭代 + UI 重构）

## 1. 目标与结论
- 已将入口从「IDE/终端优先」重构为「手机工作台优先」。
- 当前主入口为 `/`（手机单栏 Codex Mobile 控制台）。
- `/term/` 保留为 ttyd 终端兜底入口。
- `/ide/` 保留为 code-server 桌面 IDE 备用入口。
- 已移除对 Tailscale Serve 的运行依赖（`tsu serve status` 为 `No serve config`）。

## 2. 当前架构
- 访问链路：
- 手机浏览器 -> VPN -> `http://<server_vpn_ip>:80` -> `webterm-nginx`
- 反代路由：
- `/` -> 手机前端静态页面（`mobile_console/frontend`）
- `/api/` -> `codex-bridge`（FastAPI，UNIX socket）
- `/term/` -> `/ttyd/`（ttyd）
- `/ide/` -> code-server
- `/code/`（旧入口）-> 重定向到 `/`（避免误进 IDE）
- 手机 UA 访问 `/ide/` 会自动回到 `/`（桌面端仍可访问 `/ide/`）
- 内部服务暴露方式：
- `ttyd`：`~/.local/run/ttyd/ttyd.sock`
- `code-server`：`~/.local/run/code-server/code-server.sock`
- `codex-bridge`：`~/.local/run/codex-bridge/codex-bridge.sock`

## 3. 已实现能力（MVP）
- 手机前端（单栏 + 底部导航）：
- UI 结构重排（借鉴 remotelab）：
- 顶栏：状态 + 当前项目/会话标题
- 左侧抽屉：项目切换、会话历史、会话操作、项目创建
- 主视图：聚焦聊天输出与任务输入，减少同屏干扰按钮
- 底部导航：项目 / 会话 / 变更 / 文件 / 运行 / 更多
- Dashboard：仓库状态
- Dashboard：支持“新建项目（本地 + GitHub）”表单
- Chat：发送任务 + SSE 实时输出
- Chat：可查看当前项目历史会话列表，并按会话恢复
- Chat：新增“同步会话 / 重发上条 / 一键模板任务（继续推进、总结进展、修复并测试、变更摘要）”
- Chat：新增会话元信息条（会话ID、shared/temp、状态、更新时间）
- Chat：会话元信息新增执行模式展示（巡检 / 开发 / 全自动 / 外部）
- Chat：新增“重命名会话”“复制会话链接（repo+session 深链接）”
- Chat：新增 URL 直达（`?repo=<id>&session=<id>&page=chat`）与会话草稿/未发送消息保护（弱网时不丢）
- Chat：SSE 增量输出已优化，降低 tmux 屏幕重绘导致的“刷屏/看不清输出”
- Chat：新增结构化时间线，优先展示 Codex 会话 JSONL 中的任务 / 推理 / 工具调用 / 输出 / 最终答复
- More：新增三档执行模式开关，恢复共享会话时可自动按新模式重建
- More：新增每项目两条自定义快捷任务，可持久化到本地存储
- Approval 卡片：批准/拒绝（基于输出关键字识别）
- Changes：Git status + diff
- Files：最近文件 + 树 + 文件读取/保存
- Run：常用命令按钮
- More：跳转 `/term/`、`/ide/`
- 后端 `codex-bridge`：
- 仓库管理：`/api/repos`、`/api/repos/open`
- 多项目发现：自动扫描 `CODEX_WORKSPACE_ROOT`（当前为 `~/codex`）下项目并展示
- 项目初始化：`POST /api/projects/init`（创建本地项目、可选创建 GitHub repo、配置 origin、首推 main）
- 会话管理：`new/resume/list`
- 会话恢复：`POST /api/sessions/{session_id}/resume`
- 会话重命名：`POST /api/sessions/{session_id}/rename`
- 任务输入：`/api/sessions/{id}/prompt`
- 会话快照：`GET /api/sessions/{id}/snapshot?lines=...`（用于跨端同步上下文）
- 实时流：`/api/sessions/{id}/stream`（SSE）
- 结构化事件：自动关联 `~/.codex/sessions/*.jsonl`，在快照和 SSE 中回传 timeline 事件与模型元数据
- 启动模式：`inspect` / `workspace` / `full-auto`，通过 `codex-mobile --mobile-mode` 注入对应 sandbox/approval 策略
- 批准接口：`approve/reject`
- Git：status/diff/diff file
- 文件：recent/tree/read/write
- 运行：run/cmd、run/test
- 会话持久化：tmux 会话命名
- 共享会话：`codex_<repo_id>_shared`（`resume` 默认进入）
- 临时会话：`codex_<repo_id>_<随机后缀>`（`new` 创建）

## 4. 关键文件
- 前端：
- `mobile_console/frontend/index.html`
- `mobile_console/frontend/app.css`
- `mobile_console/frontend/app.js`
- 后端：
- `mobile_console/backend/app/main.py`
- `mobile_console/backend/app/session_manager.py`
- `mobile_console/backend/app/tmux_adapter.py`
- `mobile_console/backend/app/event_parser.py`
- `mobile_console/backend/app/git_service.py`
- `mobile_console/backend/app/file_service.py`
- 部署与配置：
- `scripts/deploy_mobile_console.sh`
- `mobile_console/nginx/mobile-console.conf`
- `mobile_console/systemd/codex-bridge.service`
- `mobile_console/scripts/codex-mobile`

## 5. 运行状态（已核对）
- `systemctl --user is-active ttyd-codex.service` -> `active`
- `systemctl --user is-active code-server-codex.service` -> `active`
- `systemctl --user is-active codex-bridge.service` -> `active`
- `docker ps` 中 `webterm-nginx` 正常监听 `0.0.0.0:80`
- 本地冒烟：
- `/` -> 200（移动前端）
- `/api/healthz` -> `{"status":"ok"}`
- `/api/repos` -> 正常返回仓库
- `/term/` -> 302 到 `/ttyd/`
- `/ide/` -> 302 到 code-server workspace

## 6. 认证与安全
- Nginx 统一 Basic Auth，当前固定账号密码为 `chd/chd`（按当前需求写死）。
- 当前凭证保存在：`~/.local/share/web-terminal/mobile_auth.env`
- htpasswd 文件：`~/.local/share/web-terminal/nginx/htpasswd_ttyd`
- 内部服务均通过 UNIX Socket，不直接对外监听 TCP 端口。
- 查看当前账号密码：
```bash
cat ~/.local/share/web-terminal/mobile_auth.env
```

## 7. 发布与运维
- 一键部署：
```bash
cd /home/haodong_chen/ml_com
bash scripts/deploy_mobile_console.sh
```
- 可选：部署时注入 GitHub 自动建仓权限（推荐）：
```bash
cd /home/haodong_chen/ml_com
GITHUB_OWNER=<your_github_user_or_org> \
GITHUB_TOKEN=<your_pat_with_repo_scope> \
bash scripts/deploy_mobile_console.sh
```
- 当前项目根目录：
```bash
grep '^CODEX_WORKSPACE_ROOT' ~/.local/share/codex-mobile/codex-bridge.env
```
- 查看服务：
```bash
systemctl --user status ttyd-codex.service
systemctl --user status code-server-codex.service
systemctl --user status codex-bridge.service
docker ps | rg webterm-nginx
```
- 查看日志：
```bash
journalctl --user -u codex-bridge.service -f
journalctl --user -u ttyd-codex.service -f
journalctl --user -u code-server-codex.service -f
docker logs -f webterm-nginx
```
- `codex-bridge` 无响应/`/api` 502 的快速恢复：
```bash
systemctl --user kill codex-bridge.service
systemctl --user start codex-bridge.service
```
- 说明：当前 `codex-bridge` 服务已加入 `--timeout-graceful-shutdown 8` 和 `TimeoutStopSec=10`，可避免历史上“服务在停止中卡死、socket 不监听导致 502”的问题。

## 8. 与目标设计的差距（未完成）
- 防火墙“仅允许 VPN 网段访问 80/443”未自动落地。
- 原因：当前流程无 sudo/root 防火墙变更步骤。
- 建议后续补充（需 sudo）：`ufw`/`iptables` 按 VPN CIDR 限制。
- Approval 目前为输出关键字识别，不是 Codex CLI 结构化原生审批事件。
- 结构化时间线当前采用“Codex session JSONL 自动匹配 + tmux 日志兜底”的兼容方案；多会话并发时仍可能需要继续提升匹配精度。
- 执行模式当前主要影响“新建临时会话”和“恢复共享会话”；已运行中的普通临时 tmux 会话不会被后台强制切模。
- 前端 UI 已是 mobile-first，但仍是 MVP，未实现完整历史检索与复杂摘要卡片。

## 9. 当前访问方式
- 手机主入口（推荐）：`http://<server_vpn_ip>/`
- 终端兜底：`http://<server_vpn_ip>/term/`
- IDE 备用：`http://<server_vpn_ip>/ide/`

## 10. 多端一致性建议（新增）
- 手机与 Mac 想保持同一项目会话连续：都使用“恢复会话（resume）”，默认会回到该项目 `shared` 会话。
- 后端会自动发现该项目下正在运行的 tmux 会话（按会话名前缀或当前工作目录识别），因此在 Mac 上开的同项目 tmux 会话会自动出现在手机会话列表。
- 注意：如果 Mac 端不是在 tmux 中运行（仅普通 SSH shell 直接跑 codex），手机端无法附着该进程；建议在 tmux 中运行。
- 对 VSCode 插件会话：后端会扫描 `~/.codex/sessions/*.jsonl` 中 `originator=codex_vscode` 的会话，并作为 `vscode` 类型显示在手机会话列表。点击后会在手机侧启动 `codex resume <session_id>` 接续同一会话语境。
- 手机端若检测到更新的会话，会显示“切到最新会话”按钮；可一键追平。
- 页面刷新或设备切换后，Chat 会先拉取 `/snapshot` 再接 SSE，减少“只看到局部输出”的问题。
