---
name: LinkRemote Native Account Workspace
description: 既有 Flutter 桌面明暗主题中的账号工作区扩展（SCAN 记录）
colors:
  action-blue: "#0071FF"
  material-primary: "#2196F3"
  label-accent: "#0071FFAA"
  scaffold-light: "#FFFFFF"
  scaffold-dark: "#18191E"
  field-card-light: "#EFEFF2"
  field-card-dark: "#24252B"
  hover-light: "#E0E0E0"
  hover-dark: "#2D2E35"
  body-light: "rgba(0, 0, 0, 0.87)"
  secondary-dark: "rgba(255, 255, 255, 0.70)"
  current-session-light-bg: "#E8F1FF"
  current-session-light-fg: "#1556A0"
  current-session-dark-bg: "#12304F"
  current-session-dark-fg: "#B6DBFF"
typography:
  title:
    fontSize: "19px"
  section-title:
    fontSize: "19px"
    fontWeight: 600
  body:
    fontSize: "14px"
    lineHeight: 1.25
  small:
    fontSize: "12px"
    lineHeight: 1.25
  label:
    fontSize: "16px"
rounded:
  badge: "6px"
  control: "8px"
  section: "12px"
  movable-dialog: "14px"
  theme-dialog: "18px"
spacing:
  compact: "8px"
  related: "12px"
  content: "16px"
  section-gap: "20px"
  page: "24px"
components:
  button-primary:
    backgroundColor: "{colors.action-blue}"
    rounded: "{rounded.control}"
  button-outlined-light:
    backgroundColor: "{colors.field-card-light}"
    textColor: "{colors.body-light}"
    rounded: "{rounded.control}"
  button-outlined-dark:
    backgroundColor: "{colors.field-card-dark}"
    textColor: "{colors.secondary-dark}"
    rounded: "{rounded.control}"
  current-session-light:
    backgroundColor: "{colors.current-session-light-bg}"
    textColor: "{colors.current-session-light-fg}"
    rounded: "{rounded.badge}"
    padding: "6px 8px"
  current-session-dark:
    backgroundColor: "{colors.current-session-dark-bg}"
    textColor: "{colors.current-session-dark-fg}"
    rounded: "{rounded.badge}"
    padding: "6px 8px"
  account-section:
    rounded: "{rounded.section}"
    padding: "16px"
---

# Design System: LinkRemote Native Account Workspace

## Overview

**Creative North Star: "既有原生桌面工作区"**

本记录描述已构建的 Flutter 桌面“我的”与设备快捷弹框，沿用 LinkRemote 的蓝色操作、系统文字和明暗主题。它是既有产品语言的功能扩展，采用用户批准的 A 紧凑账号摘要、B 通栏列表、C 明确概念区分组合；上述名称是记录用描述，不是新品牌定位。

范围依据 `flutter/lib/common.dart`、`common/widgets/account_workspace.dart`、`common/widgets/desktop_movable_dialog.dart` 及桌面主标签源码。`PRODUCT.md` 和 `docs/superpowers/specs/2026-10-25-account-workspace-design.md` 提供产品与批准边界，实际数值以当前实现为准。Web 账号应用保留独立 `app.css` 身份，本文件不将原生样式强加到 Web。

**Key Characteristics:**
- 紧凑账号摘要与通栏分区，账号登录和远控登记分别表达。
- 蓝色操作强调、主题表面与细分隔线，避免品牌替换。
- 窄窗纵向滚动，弹框标题拖动与内容滚动分工明确。

## Colors

蓝色承担操作和当前会话提示；中性表面承担结构。frontmatter 数值来自当前源码，Flutter 逻辑像素以可移植 px 表示。

### Primary
- **操作蓝**（`action-blue`）：`MyTheme.accent`，用于提升按钮和既有连接操作；并非所有 Material 控件的默认主色。
- **Material 主蓝**（`material-primary`）：明暗 `ColorScheme.primary` 均为 `Colors.blue`，与操作蓝保留为两个实际角色。
- **半透明标签蓝**（`label-accent`）：源值 `0xAA0071FF`，用于主题 `labelLarge`。
- **当前会话蓝色对**：明暗各自使用对应的背景/前景 token。两对对比度分别为 6.43:1、9.33:1；这是这两对颜色的计算值，不是整页或 Web 数值验收结论。

### Neutral
- **窗口底色**（`scaffold-light` / `scaffold-dark`）：主题 scaffold 与普通 dialog 的显式底色。
- **表单与主题卡片底色**（`field-card-light` / `field-card-dark`）：输入填充、outlined 按钮和主题 `cardColor`；不要据此误读“我的”分区底色。
- **悬停中性灰**（`hover-light` / `hover-dark`）：沿用主题 hover 角色，实际控件状态由 Flutter 解析。
- **正文与次级文字**（`body-light` / `secondary-dark`）：明亮正文、暗色 tab/按钮次级文字的显式主题值；暗色正文继续继承主题。

“我的”卡片实际使用 `colorScheme.surface`，页底为 `onSurface` 以 0.035 alpha 混合在 `surface` 上。移动弹框也使用 `surface`。这些是运行时主题角色，未另造固定色；不能把 scaffold、cardColor、surface 混成一个 token。

## Typography

保留系统/Flutter 现有字体解析；所查主题未指定新的文本 `fontFamily`，不引入品牌字体。页面“我的”继承 `headlineSmall` 并加到 600；账号摘要继承 `titleMedium`，不虚构它们的固定字号。

标题（19）、正文（14 / 行高 1.25）、小字（12 / 行高 1.25）和标签（16）来自主题。分区标题在 `titleLarge` 上加到 600；设备名也使用 600。暗色 `labelLarge` 另有 bold，其他继承值不能据此全局加粗。时间信息以小字显示，未知时间和平台用明确文案表达。

## Layout

工作区采用单列、居中、最大宽（1140），页面内边距（24）。账号摘要后，各分区顶部间距（20），分区内边距（16），标题到内容（12），设置入口顶部（24）。全文包在 `SingleChildScrollView` 中。

摘要与会话行均在各自可用内容宽达到（760）时切换布局，而非按整个窗口宽直接判断。宽摘要横排账号与操作；窄摘要改纵排，按钮 `Wrap` 间距（12）、换行（8）。宽会话行时间列宽（285）；窄时移到设备说明下，右侧状态/操作保留。不要用桌面固定列宽替代窄窗布局。

快捷弹框最大尺寸（570 × 560）；实际宽高各取最大值与窗口可用尺寸减（32）的较小值，初始居中。拖动位置与窗口缩小时的位置都限制在当前窗口内。标题和底部动作固定，中间内容独立保留滚动交互。

原生观察参考：`.impeccable/review/native-fix-verdict/my-1280-light.png`、`my-1280-dark.png` 和 `.impeccable/review/native-round2/dialog-700-light.png`、`dialog-700-dark.png`。这些为合成账号数据的原生渲染证据，不是已安装客户端或实际硬件验收，也不替代键盘、拖动、1.3 倍文字的行为测试。

## Elevation & Depth

工作区分区以主题表面、页底轻微色差和分隔线区分，无额外自定义卡片阴影。可移动弹框使用 Flutter `Material` elevation（12），普通主题对话框 elevation（15）；保留 Flutter 原生阴影模型，不编造对应 CSS box-shadow 数值。主题 AppBar 阴影透明。

## Shapes

当前会话标记是轻圆角（6）；提升/outlined 按钮、输入边框（8）；工作区分区与设置容器（12）；可移动弹框（14）并抗锯齿裁切；普通主题对话框与桌面 text button（18）。这些角色有意分开，不统一成一个圆角。暗色 outlined 按钮边线为白色 12% / 宽 0.5，普通对话框另有主题边框。

## Components

### Buttons

账号摘要复用 ElevatedButton 登录、OutlinedButton 注册/更换邮箱/重置密码、TextButton 退出本账号；会话行复用 OutlinedButton 退出登录。保持主题明暗样式与禁用状态，按钮 padding 等未覆盖项继续继承 Flutter。桌面禁用波纹/高亮 splash，不新增自定义动效。

### Chips

“当前会话”是非操作状态标记，内边距为横（8）纵（6），使用对应主题蓝色对，不能以“退出登录”按钮代替。

### Cards / Containers

账号摘要、登录设备、远控设备和设置入口共用轻圆角主题表面。登录设备分区保留刷新 tooltip、加载进度、空态与错误态；错误使用 `colorScheme.error`，并通过 live region 表达。

### Inputs / Fields

复用 dense、filled、outline 输入主题。指定会话退出的密码框遮蔽输入、关闭建议与自动更正，提交中禁用；Enter 走同一提交方法。确认对话框内容可滚动，错误文案留在框内。沿用 Flutter 焦点表现，不另设品牌焦点环。

### Navigation

桌面初始主标签为“远程控制 / 我的”，默认远程控制；“我的”底部设置入口继续打开既有设置页，深链也可打开设置标签。保留既有 tab 图标与选中状态，不把效果图的侧栏或附带图标当作批准范围。

### Movable device dialog

只有标题区域发起拖动，带拖动符号、move 光标和带“关闭”tooltip 的关闭按钮。标题 padding 为（16, 8, 8, 8），正文（16），底部（12）；底部 OverflowBar 间距和换行间距均为（8）。Esc 调用取消，焦点作用域为 closedLoop，Tab 遵循 Flutter 遍历。内容滚动不发起标题拖动；可视边界约束须保留。

## Do's and Don'ts

### Do:
- **Do** 沿用原生明暗主题、系统文字与蓝色操作角色。
- **Do** 将登录会话与远控登记分开，保留作用说明与未知信息文案。
- **Do** 保留窄窗换行、页面/正文滚动、可达的弹框关闭区和键盘焦点边界。

### Don't:
- **Don't** 把主题 surface、scaffold 和 cardColor 当作同一背景。
- **Don't** 用合成原生截图或 sidecar 示意组件声称已安装客户端、真机或 Web 数值验收通过。
- **Don't** 将本次原生工作区记录扩张为品牌重设或 Web `app.css` 重写。
