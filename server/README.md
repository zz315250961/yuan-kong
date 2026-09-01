# LinkRemote 自建服务

本目录包含远控信令/中继配置，以及仅用于个人账号与设备列表管理的 Web 服务。Web 管理端不提供浏览器远控、文件传输、终端或摄像头访问，因此无需开放 21118/21119。

## 服务边界

- hbbs/hbbr：沿用 `docker-compose.yml`，只开放 21115/TCP、21116/TCP+UDP、21117/TCP。
- 账号 API 与管理页：`linkremote-api/app.py`，仅监听服务器本机 21114，由 Nginx 通过 HTTPS 反向代理。
- `data/`、`smtp.json`、数据库和凭据不提交到仓库。

## 账号服务配置

生产环境至少设置：

```text
LINKREMOTE_PORT=21114
LINKREMOTE_ALLOWED_ORIGIN=https://remote.example.com
LINKREMOTE_COOKIE_SECURE=1
```

在 `server/linkremote-api` 目录使用 Python 3 启动 `app.py`。服务启动前会创建数据库；SMTP 配置以 `smtp.json.example` 为模板，密码只保存在服务器的 `smtp.json`。

Nginx 示例见 `nginx/linkremote-web.conf.example`。替换示例域名和证书路径后再启用，管理端只能通过 HTTPS 对外提供。

健康检查：

```bash
curl --fail https://remote.example.com/api/health
```

## 数据备份与恢复

备份前暂停账号 API，复制 `linkremote-api/data/linkremote.db` 到受控备份目录，再恢复服务。恢复时同样先暂停服务，把备份文件复制回原路径并确认属主/权限，然后启动服务并执行健康检查。不要只复制 WAL 临时文件，也不要把数据库提交到 Git。

## 发布前浏览器验收

使用临时数据库和 `LINKREMOTE_COOKIE_SECURE=0` 在 localhost 验证：注册/登录、刷新页面保持登录、设备空状态、密码重置、邮箱更换、退出、401 返回登录、360px 布局和键盘焦点。页面中不应出现“连接”或浏览器远控入口。

本仓库交付配置和测试，不自动上传或部署到生产服务器。
