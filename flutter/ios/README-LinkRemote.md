# LinkRemote iOS 控制端

iOS 端复用 Flutter 远程会话与 Rust 解码核心，只作为 iPhone/iPad 控制 Windows、Android 的客户端。系统不允许普通 App 提供与桌面端相同的无人值守被控能力，因此 iOS 首页不显示被控服务入口。

## 构建

需要 macOS、Xcode、CocoaPods、Flutter 3.24.5、Rust iOS `aarch64-apple-ios` 目标和项目依赖。仓库根目录执行：

```bash
./flutter/build_ios.sh --unsigned
```

这会先构建 Rust 静态库，再执行 Flutter 的无签名设备构建；只能用于构建验证，不能直接安装到他人设备。安装/TestFlight/App Store 交付需 Apple Developer 团队、`top.zperme.linkremote` App ID 和对应证书/描述文件；在 Xcode 的 Runner Signing & Capabilities 选择自己的团队后执行 `./flutter/build_ios.sh`。不要提交证书、描述文件、私钥或开发者账号凭据。

## 发布前验收

- iPhone 与 iPad：登录、设备列表、ID 连接、剪贴板与输入、横竖屏、安全退出。
- 本地网络和蜂窝网络：直连/中继切换、断线重连、后台再进入。
- 1080p 连接：观察解码帧率、延迟、耗电和发热；120 FPS 只在设备显示、编解码和网络都支持时作为可选目标，不能承诺恒定达到。
- 检查应用名称、图标、`linkremote://` 深链和相机/照片权限说明。
