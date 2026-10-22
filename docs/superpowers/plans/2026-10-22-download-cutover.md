# LinkRemote 1.0.44 Download Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按用户 2026-10-22 的明确选择，将 Windows/Android 正式下载入口切至已核验的 1.0.44，保留旧包和回滚能力；iOS 仅准备本地免费个人签名测试材料。

**Architecture:** 只更新静态下载包、稳定别名、现有版本清单和根首页版本文案。先只读核对实际生产路径并备份，以同文件系统临时文件及重命名切换；不重启服务、不改 API 代码或生产数据。

**Tech Stack:** PowerShell/OpenSSH、现有 Nginx、JSON、Python 标准库只读检查。

**Spec:** 本会话用户明确回答“直接将 Windows/Android 正式下载入口替换为 1.0.44”和“先只做免费个人签名测试，不购买会员”；已有验收边界见 `docs/qa/1.0.44-validation.md`。

## Global Constraints

- 候选来源固定 `4da5545a7315d7d26b9be3ce86ee88d03d39a360` / run `39733543702`；不改原件或 provenance。
- Windows 未签名，Android 已验签；发布操作不代表真机输入、语音、120 FPS 或 iOS 黑纹验收通过。
- 不删除旧版本；不改数据库、SMTP、远控密钥、Nginx 或账号 API 代码；不升级本机安装。
- 不提交或输出服务器/Apple 凭据；不购买会员、代签法律协议或操作 Apple 账号。
- iOS 更新接口保持空链接；本地 IPA 仍未签名，须本人在本机签名后才能安装。

## Review Focus

- 同名产物已存在：先核大小/摘要，不覆盖不同内容。
- 别名可能为软链接：备份保存真实文件类型、目标、权限与属主。
- 运行时清单路径可能被环境覆盖：先只读确认实际 API 环境和工作路径。
- 部分切换或网络失败：清单/首页最后替换，失败时按备份恢复；保留精确回滚路径。
- 浏览器/中间缓存：普通 URL、no-cache 和唯一 query 下载摘要复核，不仅看 HEAD/长度。

## Task 1: 同步本地发布配置

**Files:** `server/linkremote-api/release.json`、`server/site-root/index.html`。

- [x] 先运行目标 1.0.44 断言，确认当前 1.0.42 使其失败。
- [x] 只将清单两条具名 URL 改为 1.0.44，根首页改为 `v1.0.44 · 2026-10`，保留现有稳定别名和 iOS 状态。
- [x] 目标断言、`.github/tests/test_release_scope.py -v`（16 项）、临时 API 全套（26 项）与 `git diff --check` 全通过。

## Task 2: 生产下载切换

**Interfaces:** 输入为原始 EXE/MSI/APK、Task 1 两个配置；输出为 HTTPS 1.0.44 下载及可恢复备份。

- [x] 只读确认 `/var/www/zperme.top/download`、实际 manifest、别名类型、根首页、空间及同名文件。
- [x] 在受控备份目录保存原清单、根首页、两个别名（含软链语义）和 SHA/权限记录。
- [x] 上传到唯一同目录暂存位置；逐项验证精确大小及 SHA-256，然后无冲突落地三份具名包。
- [x] 依次原子切换 Windows/Android 别名、实际 manifest、根首页；不重启服务。
- [x] 独立 HTTPS 完整核验五个主入口和三个额外缓存组合；小接口/首页/健康检查通过。
- [ ] Android 额外 query 组合完整摘要：传输未完成，不计通过，不扩大为全部网络链路可靠。
- [x] 已实现切换异常时的自动恢复顺序并保留备份；本轮切换检查成功，未实际回滚演练。

## Task 3: iOS 免费测试交付及记录

**Interfaces:** 输入为未改动的 633224d device 原 ZIP；输出仅本地待签名 IPA，不是可直接安装包。

- [x] 将原 ZIP 266 个成员仅加 `Payload/` 前缀，原内容、Unix 权限和压缩数据保持不变。
- [x] 独立核验全部映射、CRC、身份、arm64、未签名状态及原件不变。
- [x] 准备第三方 Windows 免费签名工具的官网入口和本地说明；不操作 Apple 账号。
- [ ] 用户本人完成 Apple 登录/2FA/协议、设备信任、开发者模式和实际签名安装。
- [x] 保存发布核验、回滚位置和未通过的真机边界，并由独立代理复核配置范围和公网小接口。

## Execution rulings

- 用户已明确选择直接切正式入口，按该指令执行，不再次要求确认同一发布选择。
- 当前是现有功能的发布配置切换，沿用现有工作分支；不创建新产品功能、重建候选或扩大到账号 API 部署。
- 五个正式主入口完整 HTTPS 摘要均已匹配；额外网络探针的未完成传输不冒充通过，也不据此推断生产文件内容错误。保留 Android query 未验证项，而不是反复扩大下载探针时长。
