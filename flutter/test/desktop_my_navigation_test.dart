import 'package:flutter/material.dart';
import 'package:flutter_hbb/desktop/pages/desktop_main_tabs.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets(
      'default navigation exposes remote and My, never precreates settings',
      (tester) async {
    final tabs = buildDesktopMainTabs(
        home: const Text('remote-body'), my: const Text('my-body'));
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: Column(children: [
      for (final tab in tabs)
        TextButton(onPressed: () {}, child: Text(tab.label))
    ]))));
    expect(find.text('远程控制'), findsOneWidget);
    expect(find.text('我的'), findsOneWidget);
    expect(find.text('Settings'), findsNothing);
    expect(tabs.every((tab) => !tab.closable), isTrue);
    expect(tabs.length, 2);
  });
}
