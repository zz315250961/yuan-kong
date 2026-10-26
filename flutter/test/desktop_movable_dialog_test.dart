import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_hbb/common/widgets/desktop_movable_dialog.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  Future<void> show(WidgetTester tester, VoidCallback close) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(700, 600);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
        builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context)
                .copyWith(textScaler: const TextScaler.linear(1.3)),
            child: child!),
        home: Scaffold(
            body: DesktopMovableDialog(
                title: const Text('我的设备'),
                onCancel: close,
                content: ListView(
                    children: List.generate(
                        20, (i) => ListTile(title: Text('演示设备 $i')))),
                actions: [
              TextButton(onPressed: close, child: const Text('关闭'))
            ]))));
    await tester.pumpAndSettle();
  }

  testWidgets('title drags but content scroll leaves the dialog fixed',
      (tester) async {
    await show(tester, () {});
    final before = tester.getTopLeft(find.text('我的设备'));
    await tester.drag(find.text('我的设备'), const Offset(25, 20));
    await tester.pumpAndSettle();
    expect(tester.getTopLeft(find.text('我的设备')), before + const Offset(25, 20));
    final moved = tester.getTopLeft(find.text('我的设备'));
    await tester.drag(find.byType(ListView), const Offset(0, -120));
    await tester.pumpAndSettle();
    expect(tester.getTopLeft(find.text('我的设备')), moved);
    expect(tester.takeException(), isNull);
  });
  testWidgets('all edges and resize keep title and close reachable',
      (tester) async {
    await show(tester, () {});
    for (final delta in [
      const Offset(4000, 4000),
      const Offset(-4000, -4000)
    ]) {
      await tester.drag(find.text('我的设备'), delta);
      await tester.pumpAndSettle();
      expect(tester.getRect(find.text('我的设备')).left, greaterThanOrEqualTo(0));
      expect(tester.getRect(find.text('关闭')).bottom, lessThanOrEqualTo(600));
    }
    tester.view.physicalSize = const Size(400, 400);
    await tester.pumpAndSettle();
    expect(tester.getRect(find.text('关闭')).right, lessThanOrEqualTo(400));
    expect(tester.getRect(find.text('关闭')).bottom, lessThanOrEqualTo(400));
    expect(tester.takeException(), isNull);
  });
  testWidgets('escape and close action invoke cancel, keyboard remains usable',
      (tester) async {
    var closed = 0;
    await show(tester, () {
      closed++;
    });
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.sendKeyEvent(LogicalKeyboardKey.escape);
    expect(closed, 1);
    await tester.tap(find.text('关闭'));
    expect(closed, 2);
  });
}
