# LinkRemote 远控

LinkRemote 是面向个人设备的自建远程控制客户端。当前产品只发布以下三个端：

- Windows x64：远程控制、被控、文件与剪贴板等桌面能力。
- Android arm64：控制电脑、被控以及设备列表。
- Web 管理端：账号注册、登录、邮箱安全与设备管理；不提供浏览器远控。

## 产品特点

- 10–60 FPS 自适应帧率，根据网络状态平稳升降档。
- 码率、分辨率和帧率协同调整，优先保证操作连续性与文字清晰度。
- 使用自己的 ID、中继和账号服务，账号数据与服务密钥自行保管。
- LinkRemote 独立名称、图标、Android 包名和 Windows 程序标识。

## 目录

```text
flutter/                 Windows 与 Android 的 Flutter 界面
src/                     远控核心与平台能力
libs/                    协议、采集、输入及共享组件
server/linkremote-api/   账号、设备和产品网站服务
.github/workflows/       Windows、Android、Web 的构建与发布
```

## 构建与测试

```powershell
python -m unittest discover -s server/linkremote-api/tests -v
cargo test --test video_qos
```

完整客户端构建需要 Rust、Flutter、Android NDK 和 Windows vcpkg 依赖。正式发布工作流位于 `.github/workflows/linkremote-release.yml`。

## 开源许可

本项目是基于 RustDesk 社区版持续修改的派生作品，按 GNU Affero General Public License v3.0 发布。完整条款见 [LICENCE](LICENCE)，对应源码通过本仓库提供。

LinkRemote 的名称、图标、网站视觉和新增业务代码属于本项目；底层开源代码原作者的版权声明继续保留。未经授权访问或控制他人设备可能违法，请只连接自己拥有或已获明确授权的设备。
