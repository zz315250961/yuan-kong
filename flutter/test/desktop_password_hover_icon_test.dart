import 'package:flutter/material.dart';
import 'package:flutter_hbb/desktop/pages/desktop_home_page.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';

void main() {
  for (final enabled in [false, true]) {
    testWidgets(
        'password copy enabled=$enabled renders without GetX ErrorWidget',
        (tester) async {
      final dynamic state = const DesktopHomePage().createState();
      final hover = false.obs;
      var taps = 0;
      await tester.pumpWidget(MaterialApp(
          theme: ThemeData(
              textTheme:
                  const TextTheme(titleLarge: TextStyle(color: Colors.blue))),
          home: Scaffold(
              body: Builder(
                  builder: (context) => state.buildPasswordHoverIcon(context,
                      hover: hover,
                      tooltip: 'Synthetic copy',
                      icon: Icons.copy_outlined,
                      enabled: enabled,
                      onTap: () => taps++) as Widget))));

      expect(tester.takeException(), isNull,
          reason: 'Disabled permanent-password copy must not become a '
              'GetX ErrorWidget in the native sidebar');
      expect(find.byType(ErrorWidget), findsNothing);
      final icon = find.byIcon(Icons.copy_outlined);
      expect(tester.widget<Icon>(icon).color,
          enabled ? const Color(0xFFDDDDDD) : Colors.grey);
      await tester.tap(icon);
      expect(taps, enabled ? 1 : 0);

      final ink = tester.widget<InkWell>(find.byType(InkWell));
      expect(ink.onHover, enabled ? isNotNull : isNull);
      if (enabled) ink.onHover!(true);
      await tester.pump();
      expect(tester.takeException(), isNull);
      expect(
          tester.widget<Icon>(icon).color, enabled ? Colors.blue : Colors.grey);
    });
  }
}
