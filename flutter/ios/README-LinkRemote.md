# LinkRemote iOS 控制端

iOS 端复用 Flutter 远程会话与 Rust 解码核心，只作为 iPhone/iPad 控制 Windows、Android 的客户端。系统不允许普通 App 提供与桌面端相同的无人值守被控能力，因此 iOS 首页不显示被控服务入口。

## 构建

需要 macOS、Xcode、CocoaPods、Flutter 3.24.5、Rust 1.75 的 `aarch64-apple-ios` 目标，以及按 `vcpkg.json` 安装到 `VCPKG_ROOT` 的 `arm64-ios` 原生依赖。桥接生成工具与 CI 使用相同版本：

```bash
cargo install cargo-expand --version 1.0.95 --locked
cargo install flutter_rust_bridge_codegen --version 1.80.1 --features uuid --locked
```

Intel 主机还需 NASM **2.16.03** 与 `pkg-config`。本仓库固定的 AOM 版本通过 `nasm -hf` 检测 `-Ox`，NASM 3 改变帮助布局会触发错误的“不支持 multipass optimization”判断。CI 的 `Install native build tools` 步骤从[官方源码](https://www.nasm.us/pub/nasm/releasebuilds/2.16.03/nasm-2.16.03.tar.xz)构建 2.16.03，并核对固定 SHA-256 `1412a1c760bbd05db026b6c0d1657affd6631cd0a63cddb6f73cc6d4aa616148`；本地应让同版本 `nasm` 位于 PATH 前面，不通过禁用汇编优化规避检测。`nasm -v` 应显示 2.16.03，`nasm -hf` 应包含 `-Ox`。

仓库根目录执行：

```bash
./flutter/build_ios.sh --unsigned
```

脚本会检查工程配置及应用图标、获取 Flutter 依赖、重新生成 Flutter/Rust 桥接、构建 Rust 静态库，最后构建并校验 arm64 设备应用的名称、包名和版本。这不依赖上次构建残留的桥接文件。无签名产物只能用于构建验证，不能直接安装到他人设备。

安装/TestFlight/App Store 交付需 Apple Developer 团队、`top.zperme.linkremote` App ID 和对应证书/描述文件。在 Xcode 的 Runner Signing & Capabilities 选择自己的团队后执行 `./flutter/build_ios.sh`，签名归档也会执行产物校验。不要提交证书、描述文件、私钥或开发者账号凭据。

工程配置检查可以在 Windows、Linux 或 macOS 单独运行，无需 Flutter/Xcode：

```bash
python3 flutter/ios/verify_controller.py
python3 flutter/ios/verify_controller.py --app flutter/build/ios/iphoneos/Runner.app
```

手动触发 `.github/workflows/linkremote-ios-verify.yml` 可在 macOS 完整构建；工作流会上传经过校验的 `linkremote-ios-unsigned.zip`，保留 `.app` 内文件的执行权限。构建成功不能替代签名、连接功能和真机性能验收。

工作流的 `build_target` 可选 `both`、`device`、`simulator` 和诊断专用 `simulator-ui-replay`，默认并行构建两个目标。后者严格校验指定历史 run、提交、原始 ZIP 哈希、版本与平台，只重放已有 App，不能作为当前源码的构建验收。可选 `simulator_fresh_devices=true` 仅为这次诊断创建并删除自有同型号设备，不删除 runner 预置或其他设备，不放宽启动/首帧断言。例如对当前验证分支触发两目标检查：

```bash
gh workflow run linkremote-ios-verify.yml --repo zz315250961/yuan-kong \
  --ref fix/mobile-controls-ios-1.0.43 -f build_target=both
```

只检查设备或模拟器时分别改为 `-f build_target=device` 或 `-f build_target=simulator`，`--ref` 选实际待验收的分支。该工作流构建与检查产物，不部署生产服务或发布签名 IPA。

重放可选 `replay_family=ipad` 或 `iphone`，默认仍为 `both`、各用独立 runner。`simulator_diagnostics=true` 默认关闭，仅重放模式启用只读诊断：命令起止/退出码、窄范围系统日志、宿主资源，以及真正返回 App 的命令等待 25 秒后对原 PID 的有界采样。额外两张浅色原图用于人工比较，不以像素阈值判通过；失败先保留原图，采集错误单独记录在 `diagnostics/errors.json`，不覆盖 App 主失败。55 项脚本/fixture 回归只验证接线、超时与清理，不证明已在 Mac 采得有效栈、UI 正确或音频已验收。

## 无开发者账号的模拟器验证

CI 显式使用 `macos-15-intel` 与已安装的 Xcode 16.4（iOS 18.5 SDK），避免默认 `macos-15` 标签切换 CPU 后把 x86_64 Simulator 静态库与 arm64 设备库混用。Rust 目标为 `x86_64-apple-ios`，vcpkg 使用固定基线已有的 `x64-ios` triplet（它的工具链选择 `iphonesimulator` SDK）。Runner 的链接目录由 `Flutter/NativeCore.xcconfig` 按 SDK 选择。设备构建继续使用 `aarch64-apple-ios`，两者的 `.a` 文件分别存放。

在 Intel Mac 上安装 simulator 原生依赖并运行：

```bash
rustup target add x86_64-apple-ios
"$VCPKG_ROOT/vcpkg" install --classic \
  aom:x64-ios libjpeg-turbo:x64-ios opus:x64-ios libvpx:x64-ios libyuv:x64-ios \
  aom:x64-osx libjpeg-turbo:x64-osx opus:x64-osx libvpx:x64-osx libyuv:x64-osx \
  --overlay-ports="$PWD/res/vcpkg" --x-install-root="$VCPKG_ROOT/installed"
./flutter/build_ios.sh --simulator
python3 flutter/ios/simulator_smoke.py \
  --app flutter/build/ios/iphonesimulator/Runner.app \
  --output "flutter/build/ios/simulator-smoke-$(uuidgen)"
```

`x64-ios` 是实际 Simulator 依赖，`x64-osx` 是同一 Intel 主机生成 Rust 桥接时需要的 host 依赖，两者分目录安装。仓库的 libvpx overlay 在严格 `x64-ios` 条件下选择上游真实的 `x86_64-iphonesimulator-gcc` target；设备 arm/arm64 仍沿用原路径。

烟测选择与当前 Xcode Simulator SDK 版本相同的已安装 iOS runtime 下的 iPhone 与 iPad；全量构建后的 CI 使用 `--fresh-devices` 创建同型号自有容器，仅清理本次创建的 UUID。先预热 Safari，采集原生 Settings 浅/深色对照，再首次启动 App，等待原生首个 Flutter 画面回调。App 实际顺序为 `home-light` → `home-dark`（切系统深色）→ `resumed`（恢复系统浅色，再经已预热的 Safari 切后台并返回）→ 最后才请求空 `linkremote://`，每步抓取原生截图。系统对照单独记录在 `native_screenshots`，不充当 App 截图；Safari 不用于渲染 App。原生首帧等待仍为 90 秒，CI 整个 UI 步骤最多 25 分钟，原 PID 存活断言不放宽；每设备/阶段即时输出诊断，失败时尝试保留现场截图，清理失败记录为独立错误，不能覆盖原始失败。

空深链可能出现系统的 “Open in LinkRemote?” 确认弹窗，脚本不点击确认，也不把请求成功当作应用已收到链接。报告明确 `url_delivery_verified=false`，交付及确认后的行为仍需人工验证。把深链放在最后仅隔离它对此前截图的干扰，不证明相关功能或旧 iPad 黑条已修复。截图仍需人工观察布局，不能由首帧/PID 断言推断真实远控、摄像头、蜂窝网络、温度和帧率已通过。

每次运行必须使用新输出目录；脚本会拒绝已有路径，保留旧失败或部分证据。CI 路径为 `flutter/build/ios/simulator-smoke-<run_id>-<attempt>`，对应 artifact `linkremote-ios-simulator-evidence-<run_id>-<attempt>`，包含 `report.json`、PNG 与进程/应用日志。UI 烟测之前，工作流先用 `ditto` 归档 Simulator `Runner.app` 并上传 artifact `linkremote-ios-simulator-app-<attempt>`；其内部 `linkremote-ios-simulator.zip` 保留执行权限，可供独立检查或在兼容 Intel Mac 上复用，不是 iPhone/iPad 安装版。

Simulator 模式使用软件解码、Debug Flutter，验证 UI 和启动链路。它不作为设备 Release 性能或 VideoToolbox 硬解验证。当前入口限定 Intel Mac；Apple Silicon Simulator 需同时调整原生依赖查找规则，并非把设备 arm64 库直接链接进去。

## 免费 Apple ID 的真机测试路径

当前用户尚无可用 Mac，也没有付费 Apple Developer 账号；因此目前没有可执行的本地真机签名环境。云端的无签名设备构建和 Simulator 检查不受影响，但上传的 unsigned `.app` 不是可直接安装到 iPhone/iPad 的安装版。

用户自己的 iPhone/iPad 可用 Mac 上 Xcode 的 Apple ID Personal Team 进行开发测试，不必为了本机测试先购买付费账号：在 Xcode 登录 Apple ID，打开 `flutter/ios/Runner.xcworkspace`，选择 Runner 的 Signing & Capabilities → Automatically manage signing 与自己的 Personal Team，连接并信任设备、按系统提示开启 Developer Mode，再运行 Debug。

免费团队若不能注册当前包名，使用自己唯一的临时开发包名；它会成为独立测试安装，不冒充正式升级。临时包名会使项目的正式身份校验故意失败，完成真机试验后还原；不要把该签名设置、账号或描述文件提交。Personal Team 的期限与可用能力受 Apple 限制，应由 Xcode 当前提示确认。付费分发、TestFlight 和 App Store 仍需要相应团队与签名环境；Windows 不能本地执行 Xcode 签名与 iOS Simulator。

## 原生配置范围

- 最低 iOS 13，同时包含 iPhone 与 iPad；竖屏和左右横屏均已声明，iPad 也支持倒置竖屏。
- 1.0.44 源码已把扫码接入连接首页；相机与相册经过同一校验，服务器配置码先打开可编辑设置确认，不静默改服务器。覆盖非法/超长码、自连、重复回调、取消、权限拒绝恢复及离页后回调。相册解码使用独立 isolate，限制文件和像素数。组件/模拟平台通道测试通过不等于 iOS 原生相机或真实设备已验收。本地网络权限说明用于局域网设备连接。
- `linkremote://` 由现有 `uni_links` 插件处理。`FlutterDeepLinkingEnabled=false` 明确关闭 Flutter 内置处理，避免升级 Flutter 后同一链接被两套处理器消费；不申请未实现的 Universal Links 域名能力。
- 不声明后台被控服务。系统暂停 App 后不能保证连接持续收发，返回前台的恢复行为须在签名真机验收。
- iOS 使用会话工具栏的显式剪贴板粘贴；`mChannel` 是 Android 平台通道，不把 Android 的后台剪贴板调用视为 iOS 自动同步已实现。
- 1.0.44 源码新增控制端发起双向 Voice call：工具栏入口 → 用户麦克风授权 → 原生前台音频会话 → 现有远端接受协议 → 独立 Rust CPAL 录音/Opus 上行。首段真实 PCM 成功编码后才进入已连接状态；采用 48 kHz mono、32 kbps、20 ms 包，PCM 和编码后排队均有上限。远端下行仍经现有 Opus 解码；iOS 输出回调不再休眠或动态分配，不等待解码锁。
- 语音等待 45 秒无响应会关闭；拒绝/录音失败、手动结束、断线、离页、切后台及系统音频中断都清理所属通话。授权弹窗的 inactive 状态不误当后台；回前台不自动重新开麦。通话标识过滤旧录音线程的迟到消息，启动任务可取消，不阻塞主网络循环。并非新增 iOS 被控端或被控端主动呼叫。
- 音质边界：锁定 CPAL 使用 RemoteIO，设置 `.voiceChat` 不会自动变成 VoiceProcessingIO，不能宣称已有免提 AEC/AGC。界面提示建议耳机；耳机/扬声器、蓝牙路由、音量、双向可听、来电中断及停止后麦克风指示都必须签名真机验收。参见 [CPAL 锁定实现](https://raw.githubusercontent.com/rustdesk-org/cpal/6b374bcaed076750ca8fce6da518ab39b882e14a/src/host/coreaudio/ios/mod.rs) 和 [Apple voiceChat 模式说明](https://developer.apple.com/documentation/avfaudio/avaudiosession/mode-swift.struct/voicechat)。源码与自动测试不能代替这些验收。

## 1.0.43 历史验证证据与边界（2026-10-19）

本轮已在云端 Intel macOS / Xcode 16.4 完成两个目标的真实编译；本机仍是 Windows，并未执行本地 Xcode 编译或真机签名。两个目标来源不同，不能把其中一个提交当成所有候选二进制的来源：

- 设备 Release：run [`39225991682`](https://github.com/zz315250961/yuan-kong/actions/runs/39225991682/job/117972303342) 的 `ios-device` job 成功，源码 `73522f9503d5f87a8676ce5a8364c5b44e11e8b4`。完成 `aarch64-apple-ios` Rust 核心、真实 iPhoneOS App 和版本/配置检查，并通过 65 项 Flutter 回归测试。同一 run 当时的 Simulator job 失败，不将该 run 整体记成成功。
- Simulator Debug：run [`39231661322`](https://github.com/zz315250961/yuan-kong/actions/runs/39231661322) 整体成功，源码 `6ddf347d850d739239ad7c848b3e9936cb3eef08`。真实构建 `x86_64-apple-ios` 核心与 `iPhoneSimulator` App，并核对 `top.zperme.linkremote`、`1.0.43 (75)`。iOS 18.5 下 iPhone 16 Pro 与 iPad Pro 11-inch (M4) 均出现原生首个 Flutter 画面回调；烟测结束原 PID 分别仍为 `97138` / `4370`。

官方产物已保留到工作区 `artifacts/v1.0.43-candidate/`：`LinkRemote-1.0.43-ios-unsigned.zip` 的 SHA-256 为 `9a26278f619014792c9c7f1e21d3e1623c0b82b1a6d7f5b1f65d679216f44146`，仅是无签名设备 App；`LinkRemote-1.0.43-ios-simulator-evidence.zip` 为官方 artifact `12178899508` 的原始 ZIP，SHA-256 为 `48e0c0f1eaedb41be189821e1e862680e255a4bf9651b5088cb5844f5012304c`。展开的 `ios-simulator-smoke/` 包含 8 张原生 PNG、两台设备的 Runner/进程日志和 `report.json`。

人工逐张核查后，必须收窄脚本 `passed=true` 的含义：

- 两台设备的纵向、默认字号、浅色首页已正常显示；未见控件遮挡或文字溢出。首帧与原进程存活有日志支持。
- `warm-link`、`resumed`、`home-dark` 截图仍显示系统的 “Open in LinkRemote?” 确认弹窗，脚本没有确认 Open。因此不能据此认定暖深链已交付应用，也不能认定无遮挡的后台恢复/完整深色首页已验收。
- iPad 的上述后三张截图还出现大面积黑色横条；原因尚未定位，不能忽略该视觉异常或把截图存活当成页面正确。
- 已检索两份 Runner 日志中的首帧标记，以及 Unhandled Exception、LateInitializationError、初始化失败、RenderFlex 溢出、Rust panic 和崩溃信号特征，未发现后者；这不等价于所有错误均已排除。横屏、大字号、深链确认后的行为、真实远控输入、音频、网络恢复和 120 FPS 均未由本轮 Simulator 证据验证。

调整烟测顺序、将系统弹窗移到末尾后，仅做了一次确认：run [`39238496370`](https://github.com/zz315250961/yuan-kong/actions/runs/39238496370)（源码 `295030cdeabed4ef553199d165042095b955aa51`）最终 **failure**。其中 App 编译与产物校验通过，但 UI 检查在 Simulator 基础设施准备阶段超时，尚未执行 App 安装或首帧等待：

- iPhone 16 Pro 的 `simctl bootstatus -b` 在 180 秒后超时，现场 PNG 是 SpringBoard 主屏。
- iPad Pro 11-inch (M4) 的 `simctl ui appearance light` 在 120 秒后超时，现场 PNG 全黑，非 LinkRemote 页面。
- 两台的 `failed_stage` 均为 `boot and wait for runtime`、`passed=false`、正常截图列表为空；`url_delivery_verified=false` / `url_delivery_status=not_attempted`。本轮没有新的 8 张 App 截图或 Runner 日志，不能把该结果说成 App 崩溃，也没有复核首轮黑条、无遮挡深色/恢复页面。具体 Simulator 就绪超时根因未定位，不再重跑或放宽断言。

该确认 App 的原始 `ditto` 归档为 `artifacts/v1.0.43-candidate/LinkRemote-1.0.43-ios-simulator-app.zip`（63,880,465 bytes，SHA-256 `7135bff17972ebccd90c4a9f34515dfadb8a2044ab2d0be76daa592f727b2ff4`）；只读复核 ZIP CRC、`iPhoneSimulator` / SDK 18.5 / 最低 iOS 13、`1.0.43 (75)`、x86_64 Mach-O 和执行权限 `100755` 均通过。它只供兼容 Simulator 使用，不是 iPhone/iPad 安装包。

新的官方失败证据未覆盖首轮文件：`LinkRemote-1.0.43-ios-simulator-confirmation-evidence.zip`（artifact `12183015162`，3,081,718 bytes，SHA-256 `a411b4d2320e0b4e8ba297b554155f68a7bcef1320f9499988761e61f4b9c6a7`）。展开目录 `ios-simulator-confirmation-39238496370/` 保留 `report.json`、两张 `*-failure.png`；另保存完整官方 `job.log`，可核对阶段时间与原始超时，清理过程没有覆盖主错误。

后续源码提交 `da10d14` 接入移动设置的 About 入口及离线许可/归属说明；它未进入上述 iOS 二进制，也未进入源码 `295030c` 的 Simulator 确认构建。不能用这些 App/截图宣称新增 About 或许可页面已在 iOS 运行验收。

源配置脚本检查权限说明、横竖屏、深链归属、三个构建配置的包名、部署目标，以及全部 iPhone/iPad/App Store 图标的像素尺寸和无透明通道。可选产物检查还会核对真实包名/版本、iPhoneOS 平台、arm64 Mach-O 和编译后的图标资源。

## 真机逐项验收

以下项目均须分别覆盖 iPhone 与 iPad，并分别连接 Windows 与 Android。没有设备记录与结果的项目保持“待验收”，不能以静态检查通过代替。

| 项目 | 操作与通过条件 | 当前状态 |
| --- | --- | --- |
| 账号与设备 | 登录/退出/重登、刷新设备、删除后刷新；错误密码显示可恢复提示；另核对是否提供设备改名 | 同步 23 项、API 26 项及隔离 Web E2E 通过；原生/生产页面显示仍待复核；账号设备改名入口未实现，不计通过 |
| ID 连接入口 | 输入 ID 发起连接；本地网络首次允许及拒绝后再次进入均可恢复 | 待真机验收 |
| 扫码/相册识码入口 | 入口已接入；验收相机/照片权限允许、拒绝与恢复，实际识别后仅连接一次 | 组件/平台通道回归通过，待原生与真机验收 |
| 深链 | App 关闭和已打开时各点一次 `linkremote://ID`，仅打开一次目标连接；异常 ID 不崩溃 | 解析/去重与导航门禁组件回归通过；原生冷暖交付及确认弹窗后的行为待验收 |
| 鼠标 | 触屏/触控板模式的单击、双击、双指右键、长按拖动、双指滚动；右键无多余左键与粘连 | Flutter 手势回归通过；从手机到真实远端 OS 的输入待验收 |
| 手势区分 | 双指轻点/连续轻点可右键；双指滚动、捏合缩放和中途取消均不误触右键 | 14 项手势测试通过；实际触屏与系统事件待真机验收 |
| 键盘 | 中文/英文软键盘输入、删除、回车、隐藏再唤起；iPad 外接键盘含修饰键和快捷键 | 待真机验收 |
| 剪贴板 | 显式粘贴短文本、中文和多行文本；远端禁用剪贴板时不能绕过权限 | 待真机验收 |
| 横竖屏与布局 | 在会话、键盘弹出、菜单打开时旋转；iPad 分屏改变大小，画面/按钮仍可操作且坐标正确 | 待真机验收 |
| 会话权限 | 只读模式不能发送鼠标/键盘，远端撤销输入权限后立即停止控制，恢复后可以继续 | 待真机验收 |
| 网络恢复 | Wi-Fi 直连、中继、蜂窝网络；切换网络及断网重连后无卡死或重复会话 | 待真机验收 |
| 生命周期 | 控制中锁屏、切后台、接来电再返回；无按键粘连，状态提示可恢复，退出后远端会话结束 | 待真机验收 |
| 双向语音 | 主动请求、远端接受/拒绝、双向可听；超时、结束、后台、拔出耳机和来电后停止录音，回前台不自动开麦 | 协调器 14 项、生产 Rust 逻辑 39 项通过；自动测试不替代签名真机与音质验收；建议耳机，免提 AEC 未实现 |
| 画质与性能 | 1080p 静态文字和运动画面；记录编码格式、实际接收/解码帧率、延迟及 15 分钟温度/电量 | 待真机验收 |
| 发布身份 | 签名安装后名称、图标、包名/版本符合预期；旧签名版本升级保留账号与设置 | 待 Apple 签名与真机验收 |

120 FPS 只在设备显示、编解码、远端和网络都支持时作为可选目标，不能承诺恒定达到。iOS 不提供无人值守被控端，不把该能力列入此控制端的通过条件。

验收记录应包含 App 提交/版本、iOS 版本与机型、被控端版本、网络路径、操作步骤、预期/实际结果；修复后重验失败项及相邻手势/生命周期场景。

1.0.44 候选应用源码固定为 `c0354c080a2f0b5b09c7d98b9b86e517603702ae`；上述平台名称在自动 API 测试中只是客户端元数据 fixture，不代表三台真实设备会话。最新构建、原始证据与发布闸门见 [`docs/qa/1.0.44-validation.md`](../../docs/qa/1.0.44-validation.md)。

该 SHA 的 run `39287264581` 已完成设备和 Simulator 全量编译，但 UI 检查失败：iPhone 浅/深色及恢复图正常、原 PID 保持，深链仍停在系统确认，未交付；iPad 原生 Settings 对照正常，App 浅色有黑白阶梯条纹，恢复前台超时且现场全黑。尚无渲染器、系统终止或 OOM 的确定根因，不能称 iPad 或 iOS 整体已通过，更不能以云构建代替签名真机通话。原始证据保留在工作区 `artifacts/v1.0.44-candidate/ios-c0354c-39287264581/terminal-evidence-20261020/`。
