# LinkRemote 自建服务

本目录包含远控信令/中继配置，以及仅用于个人账号与设备列表管理的 Web 服务。Web 管理端不提供浏览器远控、文件传输、终端或摄像头访问，因此无需开放 21118/21119。

## 服务边界

- hbbs/hbbr：沿用 `docker-compose.yml`，只开放 21115/TCP、21116/TCP+UDP、21117/TCP。
- 账号 API 与管理页：`linkremote-api/app.py`，原生客户端访问 21114；网页由 Nginx 通过 `https://zperme.top/remote/` 反向代理。原站 `/api/` 保留给现有应用。
- `data/`、`smtp.json`、数据库和凭据不提交到仓库。

## 账号服务配置

生产环境至少设置：

```text
LINKREMOTE_PORT=21114
LINKREMOTE_ALLOWED_ORIGIN=https://zperme.top
LINKREMOTE_COOKIE_SECURE=1
```

在 `server/linkremote-api` 目录使用 Python 3 启动 `app.py`。服务启动前会创建数据库；SMTP 配置以 `smtp.json.example` 为模板，密码只保存在服务器的 `smtp.json`。

生产 Nginx 配置见 `nginx/zperme.top.conf`；切换前先备份原配置并执行 `nginx -t`。配置保留了原站的 `/app/`、`/api/`、`/uploads/`、`/download/` 路由。网页会话 Cookie 仅在 `/remote` 路径发送。

健康检查：

```bash
curl --fail https://zperme.top/remote/api/health
```

## 数据备份与恢复

备份前暂停账号 API，复制 `linkremote-api/data/linkremote.db` 到受控备份目录，再恢复服务。恢复时同样先暂停服务，把备份文件复制回原路径并确认属主/权限，然后启动服务并执行健康检查。不要只复制 WAL 临时文件，也不要把数据库提交到 Git。

## 发布前浏览器验收

使用临时数据库和 `LINKREMOTE_COOKIE_SECURE=0` 在 localhost 验证：注册/登录、刷新页面保持登录、设备空状态、密码重置、邮箱更换、退出、401 返回登录、360px 布局和键盘焦点。页面中不应出现“连接”或浏览器远控入口。

`/version/latest?platform=windows|android|ios` 从账号服务的 `release.json` 读取各平台版本链接。只有新包已验证并上传到 `/download/` 后才更新该文件；iOS 尚无签名安装包时返回空链接。
