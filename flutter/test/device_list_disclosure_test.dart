import 'package:flutter/material.dart';
import 'package:flutter_hbb/common/widgets/device_list_disclosure.dart';
import 'package:flutter_test/flutter_test.dart';

Widget list(
        {required int count,
        Object scope = 'one',
        String unit = '台',
        ValueChanged<int>? built}) =>
    MaterialApp(
        home: Scaffold(
            body: SingleChildScrollView(
                child: DeviceListDisclosure(
                    count: count,
                    unit: unit,
                    resetKey: scope,
                    builder: (_, visible) => Column(
                            children: List.generate(visible, (i) {
                          built?.call(i);
                          return Text('远控设备 $i');
                        }))))));

void main() {
  for (final count in [0, 1, 5]) {
    testWidgets('$count records show honest counts without a redundant toggle',
        (tester) async {
      await tester.pumpWidget(list(count: count));
      expect(find.text('共$count台，显示$count台'), findsOneWidget);
      expect(find.byType(TextButton), findsNothing);
      expect(find.textContaining('远控设备 '), findsNWidgets(count));
    });
  }
  testWidgets('large initial input builds only five rows', (tester) async {
    final built = <int>[];
    await tester.pumpWidget(list(count: 10000, built: built.add));
    expect(built, [0, 1, 2, 3, 4]);
    expect(find.text('共10000台，显示5台'), findsOneWidget);
  });
  testWidgets('remote expansion resets on data scope or count changes',
      (tester) async {
    await tester.pumpWidget(list(count: 8));
    await tester.tap(find.text('展开全部（8台）'));
    await tester.pumpAndSettle();
    expect(find.text('共8台，显示8台'), findsOneWidget);
    await tester.pumpWidget(list(count: 8, scope: 'another-account'));
    expect(find.text('共8台，显示5台'), findsOneWidget);
    await tester.tap(find.text('展开全部（8台）'));
    await tester.pumpAndSettle();
    await tester.pumpWidget(list(count: 1, scope: 'another-account'));
    expect(find.text('共1台，显示1台'), findsOneWidget);
    expect(find.text('收起'), findsNothing);
  });
}
