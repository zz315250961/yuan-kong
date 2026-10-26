import 'package:flutter/material.dart';
import '../../consts.dart';
import '../widgets/tabbar_widget.dart';

List<TabInfo> buildDesktopMainTabs(
        {required Widget home, required Widget my}) =>
    [
      TabInfo(
          key: kTabLabelHomePage,
          label: '远程控制',
          selectedIcon: Icons.desktop_windows,
          unselectedIcon: Icons.desktop_windows_outlined,
          closable: false,
          page: home),
      TabInfo(
          key: kTabLabelMyPage,
          label: '我的',
          selectedIcon: Icons.person,
          unselectedIcon: Icons.person_outline,
          closable: false,
          page: my),
    ];
