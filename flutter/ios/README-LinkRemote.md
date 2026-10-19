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

工作流的 `build_target` 可选 `both`、`device`、`simulator`，默认并行检查两个目标。单项重试不需要停止另一个正在运行的任务。例如对当前验证分支触发两目标检查：

```bash
gh workflow run linkremote-ios-verify.yml --repo zz315250961/yuan-kong \
  --ref fix/mobile-controls-ios-1.0.43 -f build_target=both
```

只检查设备或模拟器时分别改为 `-f build_target=device` 或 `-f build_target=simulator`，`--ref` 选实际待验收的分支。该工作流构建与检查产物，不部署生产服务或发布签名 IPA。

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
  --output flutter/build/ios/simulator-smoke
```

`x64-ios` 是实际 Simulator 依赖，`x64-osx` 是同一 Intel 主机生成 Rust 桥接时需要的 host 依赖，两者分目录安装。仓库的 libvpx overlay 在严格 `x64-ios` 条件下选择上游真实的 `x86_64-iphonesimulator-gcc` target；设备 arm/arm64 仍沿用原路径。

烟测选择与当前 Xcode Simulator SDK 版本相同的已安装 iOS runtime 下的 iPhone 与 iPad，分别启动 App，等待原生首个 Flutter 画面回调，检查暖启动空深链和切后台再返回，抓取浅/深色原生截图及日志，确认原进程仍在运行。CI 产物 `linkremote-ios-simulator-evidence` 包含 `report.json`、PNG 与进程/应用日志。启动成功只覆盖上述范围，截图还需要人工观察布局，不能由模拟器推断真实远程控制、摄像头、蜂窝网络、温度和帧率已通过。

Simulator 模式使用软件解码、Debug Flutter，验证 UI 和启动链路。它不作为设备 Release 性能或 VideoToolbox 硬解验证。当前入口限定 Intel Mac；Apple Silicon Simulator 需同时调整原生依赖查找规则，并非把设备 arm64 库直接链接进去。

## 免费 Apple ID 的真机测试路径

用户自己的 iPhone/iPad 可用 Mac 上 Xcode 的 Apple ID Personal Team 进行开发测试，不必为了本机测试先购买付费账号：在 Xcode 登录 Apple ID，打开 `flutter/ios/Runner.xcworkspace`，选择 Runner 的 Signing & Capabilities → Automatically manage signing 与自己的 Personal Team，连接并信任设备、按系统提示开启 Developer Mode，再运行 Debug。

免费团队若不能注册当前包名，使用自己唯一的临时开发包名；它会成为独立测试安装，不冒充正式升级。临时包名会使项目的正式身份校验故意失败，完成真机试验后还原；不要把该签名设置、账号或描述文件提交。Personal Team 的期限与可用能力受 Apple 限制，应由 Xcode 当前提示确认。付费分发、TestFlight 和 App Store 仍需要相应团队与签名环境；Windows 不能本地执行 Xcode 签名与 iOS Simulator。

## 原生配置范围

- 最低 iOS 13，同时包含 iPhone 与 iPad；竖屏和左右横屏均已声明，iPad 也支持倒置竖屏。
- 相机权限用于扫描设备连接码，照片权限用于从用户选择的图片识别连接码，本地网络权限用于局域网设备连接。
- `linkremote://` 由现有 `uni_links` 插件处理。`FlutterDeepLinkingEnabled=false` 明确关闭 Flutter 内置处理，避免升级 Flutter 后同一链接被两套处理器消费；不申请未实现的 Universal Links 域名能力。
- 不声明后台被控服务。系统暂停 App 后不能保证连接持续收发，返回前台的恢复行为须在签名真机验收。
- iOS 使用会话工具栏的显式剪贴板粘贴；`mChannel` 是 Android 平台通道，不把 Android 的后台剪贴板调用视为 iOS 自动同步已实现。

## 验证证据与边界（2026-10-19）

上一次完整无签名构建为 GitHub Actions run `36308664386`（2026-09-27，提交 `ab6d9fc`），结果成功，对应 v1.0.42。该证据只适用于当时的源码。本轮工程变更需要重新运行 macOS 构建；本机为 Windows，尚未执行新 Xcode 编译。

源配置脚本检查权限说明、横竖屏、深链归属、三个构建配置的包名、部署目标，以及全部 iPhone/iPad/App Store 图标的像素尺寸和无透明通道。可选产物检查还会核对真实包名/版本、iPhoneOS 平台、arm64 Mach-O 和编译后的图标资源。

## 真机逐项验收

以下项目均须分别覆盖 iPhone 与 iPad，并分别连接 Windows 与 Android。没有设备记录与结果的项目保持“待验收”，不能以静态检查通过代替。

| 项目 | 操作与通过条件 | 当前状态 |
| --- | --- | --- |
| 账号与设备 | 登录/退出/重登、刷新设备、修改名称、删除后刷新；错误密码显示可恢复提示 | 待真机验收 |
| 连接入口 | ID、扫码、相册识码；相机/照片/本地网络首次允许及拒绝后再次进入均可恢复 | 待真机验收 |
| 深链 | App 关闭和已打开时各点一次 `linkremote://ID`，仅打开一次目标连接；异常 ID 不崩溃 | 待真机验收 |
| 鼠标 | 触屏/触控板模式的单击、双击、双指右键、长按拖动、双指滚动；右键无多余左键与粘连 | 待真机验收 |
| 手势区分 | 双指轻点/连续轻点可右键；双指滚动、捏合缩放和中途取消均不误触右键 | 待真机验收 |
| 键盘 | 中文/英文软键盘输入、删除、回车、隐藏再唤起；iPad 外接键盘含修饰键和快捷键 | 待真机验收 |
| 剪贴板 | 显式粘贴短文本、中文和多行文本；远端禁用剪贴板时不能绕过权限 | 待真机验收 |
| 横竖屏与布局 | 在会话、键盘弹出、菜单打开时旋转；iPad 分屏改变大小，画面/按钮仍可操作且坐标正确 | 待真机验收 |
| 会话权限 | 只读模式不能发送鼠标/键盘，远端撤销输入权限后立即停止控制，恢复后可以继续 | 待真机验收 |
| 网络恢复 | Wi-Fi 直连、中继、蜂窝网络；切换网络及断网重连后无卡死或重复会话 | 待真机验收 |
| 生命周期 | 控制中锁屏、切后台、接来电再返回；无按键粘连，状态提示可恢复，退出后远端会话结束 | 待真机验收 |
| 画质与性能 | 1080p 静态文字和运动画面；记录编码格式、实际接收/解码帧率、延迟及 15 分钟温度/电量 | 待真机验收 |
| 发布身份 | 签名安装后名称、图标、包名/版本符合预期；旧签名版本升级保留账号与设置 | 待 Apple 签名与真机验收 |

120 FPS 只在设备显示、编解码、远端和网络都支持时作为可选目标，不能承诺恒定达到。iOS 不提供无人值守被控端，不把该能力列入此控制端的通过条件。

验收记录应包含 App 提交/版本、iOS 版本与机型、被控端版本、网络路径、操作步骤、预期/实际结果；修复后重验失败项及相邻手势/生命周期场景。
