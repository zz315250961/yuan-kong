# Account workspace and login sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将桌面默认导航整理为“远程控制 / 我的”，提供真实账号会话管理及可移动设备弹框，保留当前远控与原安全保护。

**Architecture:** 会话管理在现有Python账号API上做加法迁移，客户端使用不可变账号/API快照防迟到写入；登录会话与登记设备分开。桌面“我的”复用账号表单和设备组件，可拖动弹框独立实现，不改变其他弹框及Android默认行为。

**Tech Stack:** Python3标准库HTTP/SQLite/unittest，Flutter3.24.5/Dart3.5.4，现有GetX与gFFI；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-25-account-workspace-design.md`（用户已批准）；`PRODUCT.md`记录产品边界。

## Global Constraints

2026-10-26 authorization addendum: user requested combining the presented comps and updating the server. Combination is recorded in the spec/ledger; server API/web deployment is now in scope, subject to reviewed helper, private code/SQLite backup, no token/config loss and public verification. Freeze a distinct1.0.46+78 candidate for new desktop binaries, never overwrite1.0.45. No restart/install of the Windows client hosting the current session; preserve native acceptance boundaries. Earlier implementation-only statements below are superseded only for this explicitly authorized server update, not for destructive live-user actions.

- 默认仅“远程控制 / 我的”，设置从“我的”进入既有受保护设置页，不预创建设置tab。
- 新功能未发布；不可覆盖1.0.45包、伪称旧客户端包含会话管理或再次发送上轮邮件。
- 原mask/AbsorbPointer/ExcludeFocus和不可变readOnly入口保留，不修改安全权限或远控协议。
- 不在测试中真实删除设备、撤销用户token、注销/重登录或关闭现有远控；不用生产DB/SMTP/凭据测试。
- 旧token值及期限保留、SQLite只做幂等加法迁移；服务端回滚不盲目还原已产生新写入的DB。
- 元数据不是授权凭证；会话API不返回token/token哈希/密码/完整IP；日志与截图不含真实账号秘密。
- 桌面700×600/1.3文字、明暗主题、键盘与滚动可用；Android共享组件原行为不变。
- 默认TUN修复独立于本计划：禁止用专用ProxyCommand/显式socket绑定冒充普通访问成功；禁止PUT重载/GUI订阅切换/清空连接。

## Review Focus

- 两个相同名称的设备与旧客户端信息未知：按opaque会话ID操作，未知不猜测、不误标在线（Task1/2/4测试）。
- 当前会话、过期会话或另一个账号的会话ID：当前拒撤销、过期过滤、跨账号不泄露（Task1/2）。
- 提交密码后切号/API变化/销毁页面：迟到结果不可更新新账号，也不可重复撤销（Task2/4）。
- 窗口缩小、拖到边缘、1.3倍文字：标题/关闭仍可达，拖动不抢按钮/列表滚动（Task3/4）。
- 远程只读入口打开后鼠标离开保护区：不得升级权限；“我的”管理不解除设置保护（Task3/4/5）。

## File boundaries

- `server/linkremote-api/account_sessions.py`：会话schema/摘要/账号隔离撤销的数据层；`app.py`保留HTTP/认证接缝。
- `flutter/lib/models/account_login_session.dart`：不含secret的会话DTO；`utils/account_session_controller.dart`：加载/撤销代次状态。
- `flutter/lib/common/widgets/desktop_movable_dialog.dart`：仅桌面设备弹框使用的可拖动、边界、焦点容器。
- `flutter/lib/desktop/pages/desktop_my_page.dart`与`common/widgets/account_workspace.dart`：实际生产“我的”页与可测试展示组件。
- 对既有`user_model.dart`、`my_devices.dart`、`desktop_tab_page.dart`、`consts.dart`仅做集成，不无关重构。

### Task1: Backend real login-session lifecycle

**Files:** Create `server/linkremote-api/account_sessions.py`、`tests/test_account_sessions.py`；modify `app.py`及`tests/test_api.py`的认证接缝。

**Interfaces:**
- `init_session_schema(conn)`幂等增加`session_id/device_name/platform/device_uuid/last_seen_at`，不改token/created_at/expires_at。
- 保留`app.create_session(conn, user_id)`调用兼容，可增加可选`metadata=None`；登录/注册带id/uuid/autoLogin的原请求响应仍兼容。
- GET `/api/sessions` -> `{"sessions":[{"id":opaque,"device_name":str,"platform":str,"created_at":str,"last_seen_at":str,"is_current":bool}]}`。
- POST `/api/sessions/{id}/revoke` body `{"password":str}` ->200 `{"revoked":true}`；未认证401、错密码403、当前会话409、账号内未知目标404、限流429。cookie认证复用既有同源/CSRF边界，Bearer旧客户端兼容。
- 会话ID为随机opaque值，非token或其hash；名称≤80、平台≤32、UUID≤128，非法类型拒绝或安全缺省；只列未过期会话，不把last_seen当在线。
- 撤销在同一事务内复核当前token仍有效、user_id与目标；当前用户+请求源的失败口令尝试每15分钟最多5次，成功不解开已触发的锁定窗口。

- [ ] 写真实旧SQLite schema迁移测试和HTTP RED：两个账号各多token，正确口令只能撤销本账号非当前目标，另一token/当前token仍可请求；断言列表文本不含任一token/password/hash；同名设备不混淆。
- [ ] 从`server/linkremote-api`运行`python -m unittest -v tests.test_account_sessions`，确认新契约缺失导致RED，记录命令/退出码，不启动生产服务。
- [ ] 实现上述最小schema与HTTP契约；旧会话未知元数据保留空值并由UI解释，不根据最近设备记录猜测归属。认证成功更新最近使用时间，不复用devices.last_login。
- [ ] 增加并运行迁移重入/旧token期限/过期过滤、错密码与畸形body/限流、跨账号、当前拒绝、并发撤销、cookie同源安全测试；运行`python -m unittest discover -s tests -v`，测试临时DB/loopback/禁用SMTP，不读取/改生产数据。
- [ ] 自审与独立安全/spec审查，修复后提交仅本Task文件，报告准确RED/GREEN和接口例子（合成数据）。

### Task2: Client session DTO, controller and account snapshot guards

**Files:** Create `flutter/lib/models/account_login_session.dart`、`flutter/lib/utils/account_session_controller.dart`、`flutter/test/account_login_session_test.dart`、`flutter/test/account_session_controller_test.dart`；modify `flutter/lib/models/user_model.dart`及必要账号测试。

**Interfaces:**
- `AccountLoginSession.fromJson(Map<String,dynamic>)`，字段`id/deviceName/platform/createdAt/lastSeenAt/isCurrent`，不含token。
- `UserModel.getLoginSessions({required AccountDeviceSession expectedSession}) -> Future<List<AccountLoginSession>>`。
- `UserModel.revokeLoginSession({required String sessionId, required String password, required AccountDeviceSession expectedSession}) -> Future<bool>`。
- Controller注入真实请求adapter与快照读取者，公开`load()`、`revoke(sessionId,password)`及只读状态`loading/error/sessions/revokingId`；密码不可成为持久状态或日志。
- 复用已有`AccountDeviceSession.matches`，所有成功/401和改邮箱返回都必须在账号/API仍匹配后才更新账号UI。

- [ ] 写DTO坏数据/未知字段与controller请求交错RED，覆盖加载失败、401、错误密码、当前会话拒绝、重复点击、同名不同ID、切号/API变更/销毁后迟到结果忽略；运行这两新测试文件确认RED。
- [ ] 实现DTO/controller与UserModel两个方法，不引入FFI诊断hook或复制真实页面作为测试假件；请求adapter为生产HTTP接缝。
- [ ] 给既有`changeEmail`补同类快照护栏，旧表单签名保持兼容；401只处理匹配的账号登录态，不关闭原生远控。
- [ ] 运行`flutter test --no-pub test/account_login_session_test.dart test/account_session_controller_test.dart test/account_device_sync_test.dart`，全部通过并记录无生产请求证据。
- [ ] 自审、独立task审查、提交仅本Task文件；接口与Task1逐字段核对。

### Task3: Scoped movable desktop device dialog

**Files:** Create `flutter/lib/common/widgets/desktop_movable_dialog.dart`、`flutter/test/desktop_movable_dialog_test.dart`；modify `my_devices.dart`及必要production layout测试。

**Interfaces:** `DesktopMovableDialog({required Widget title, required Widget content, required List<Widget> actions, required VoidCallback onCancel})`，仅桌面快捷设备弹框使用；Android与其他`CustomAlertDialog`调用不变。

- [ ] 写真实容器RED：拖动标题改变位置，拖内容不移动，按钮可点击，Esc关闭、Tab焦点留在弹框，拖过四边与窗口缩小后title/close仍可见；700×600/1.3文字无溢出。
- [ ] 实现窗口内拖动/边界、默认居中与受限尺寸；标题与刷新同排，列表独立滚动、关闭固定可达。常规桌面宽520–600按可用区域降级。
- [ ] 集成`showMyDevicesDialog({bool readOnly=false})`，原readOnly初始化一次、加载syncDevice:false、handler拒绝、incoming-only不出现例外均保留；测试跨保护状态切换不升级。
- [ ] 运行`flutter test --no-pub test/desktop_movable_dialog_test.dart test/remote_home_layout_test.dart test/remote_readonly_devices_test.dart`，记录GREEN；自审/独立审查并提交owned文件。

### Task4: “我的” page and dual main navigation

**Files:** Create `flutter/lib/common/widgets/account_workspace.dart`、`flutter/lib/desktop/pages/desktop_my_page.dart`、`flutter/test/account_workspace_test.dart`、`flutter/test/desktop_my_navigation_test.dart`；modify `desktop_tab_page.dart`、`consts.dart`、`my_devices.dart`少量文案。

**Interfaces:** 新const `kTabLabelMyPage`与`DesktopTabPage.onAddMy()`（按key复用）；`DesktopMyPage`使用Task2生产controller/adapter，Task3为快捷入口；实际展示组件接收typed会话与账号数据、真实callback，不依赖私密测试账号。

- [ ] 写生产UI/导航RED：默认仅远控与我的、无预创建设置页；设置从我的打开原页面且仍保留权限边界；未登录入口、账号摘要、两种设备区、未知会话、当前标记和正确操作均可见。
- [ ] 实现双页导航与我的账号区：复用现有登录/注册/改邮箱；密码重置显式全会话失效警告；不新增头像/昵称/注销/一键全退。设置深链保留。
- [ ] 实现逐会话退出确认+当前密码输入；当前行无退出按钮；错误/限流/401/撤销中均可理解、密码提交后清空并销毁controller，不存状态。撤销成功只去掉准确会话ID。
- [ ] 登记设备区复用既有列表/连接/复制/移除，文案“最近同步”，不伪造在线；移除提示区别退出账号。管理模式不能升级旧只读对话框或取消设置保护。
- [ ] 运行新增UI/导航、Task2/3及原只读/incoming-only测试；一次集中捕获明暗700×600/1.3、1000×700和用户截图比例的合成账号布局，必要时一次集中修正/确认，原生与合成证据分开。
- [ ] 自审、独立安全与产品可用性审查，提交owned源码/测试；使用已确认视觉开工方式，不把未答复的偏好写成用户选择。

### Task5: Integrated validation and safe delivery boundary

**Files:** New `docs/qa/account-workspace-validation.md`及本计划/新SDD记录；候选辅助产物仅新的artifact目录。

- [ ] 核Task1/2 API contract、schema回滚兼容、Task3/4权限边界和所有变更diff；跑后端全tests与Flutter全tests一次、targeted analyze及diff-check，不无变化重复全套。
- [ ] fresh reviewer终审源码/安全及有界截图；原生无新安装证据时不说实际账号管理已可用。
- [ ] 生成准确交付/部署与回滚说明：新增后端schema与私有DB备份、旧代码备份、健康检查、旧客户端兼容；禁止盲恢复新写入后的DB。当前不部署/重启生产API或安装本机，以免影响正在使用的远控会话。
- [ ] 如用户后续要求发布，先单独确认不会中断当前远控的安装/部署窗口，再冻结新版本（建议1.0.46+78，非本轮已授权版本号）、candidate-only并验收；不覆盖1.0.45、不复发上轮邮件、不恢复旧automation。
- [ ] 最终分别报告源代码/测试/截图/原生/生产的实际状态及剩余门，不把计划、自动测试或新API源码当生产完成。

## Self-review

设计中的账号/真实会话/登记设备/只读保护分别由Task1/2/4覆盖；可移动与小窗/键盘由Task3/4覆盖；当前远控连续性与新后端部署边界由Task5独立保留。DTO/API字段及状态码在上方唯一契约中固定。旧schema迁移、同名设备、竞态与限流各有所属RED/GREEN，未安排真实用户危险动作。网络线另有独立红绿探针与回滚，不能由本计划的测试替代。
