import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_hbb/common/widgets/linkremote_about.dart';
import 'package:flutter_test/flutter_test.dart';

const _captureKey = ValueKey('about-visual-capture');
final _captureDirectory = Platform.environment['LINKREMOTE_ABOUT_CAPTURE_DIR'];
final _captureFont = Platform.environment['LINKREMOTE_ABOUT_CAPTURE_FONT'];

Future<void> _capture(WidgetTester tester, String name) async {
  if (_captureDirectory == null) return;
  await tester.runAsync(() async {
    final boundary =
        tester.renderObject<RenderRepaintBoundary>(find.byKey(_captureKey));
    final image = await boundary.toImage();
    try {
      final png = await image.toByteData(format: ui.ImageByteFormat.png);
      final output = File('$_captureDirectory/$name.png');
      if (output.existsSync()) {
        throw StateError('Preserve prior capture: ${output.path}');
      }
      output.parent.createSync(recursive: true);
      output.writeAsBytesSync(png!.buffer.asUint8List());
    } finally {
      image.dispose();
    }
  });
}

void main() {
  setUpAll(() async {
    if (_captureFont != null) {
      final loader = FontLoader('AboutQa');
      loader.addFont(Future.value(
          ByteData.sublistView(File(_captureFont!).readAsBytesSync())));
      await loader.load();
      final icons = FontLoader('MaterialIcons');
      icons.addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'));
      await icons.load();
    }
  });

  test('mobile bundles the unchanged project license and attribution', () {
    for (final name in ['LICENCE', 'NOTICE.md']) {
      final asset = File('assets/$name');
      expect(asset.existsSync(), isTrue,
          reason: '$name must be usable offline');
      String normalize(String text) => text.replaceAll('\r\n', '\n').trim();
      expect(normalize(asset.readAsStringSync()),
          normalize(File('../$name').readAsStringSync()));
    }
  });

  test('project license registry retains upstream and full AGPL text',
      () async {
    registerLinkRemoteLicense();
    registerLinkRemoteLicense();
    final entries = await LicenseRegistry.licenses
        .where((entry) => entry.packages.contains('LinkRemote'))
        .toList();
    expect(entries, hasLength(1));
    final text = entries.single.paragraphs.map((p) => p.text).join('\n');
    expect(text, contains('RustDesk'));
    expect(text, contains(linkRemoteSourceUrl));
    expect(text, contains('GNU AFFERO GENERAL PUBLIC LICENSE'));
    expect(text, contains('END OF TERMS AND CONDITIONS'));
  });

  testWidgets('about page opens public source and offline licenses',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final opened = <Uri>[];
    await tester.pumpWidget(RepaintBoundary(
        key: _captureKey,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: ThemeData(fontFamily: _captureFont == null ? null : 'AboutQa'),
          home: const Scaffold(),
          initialRoute: '/about',
          routes: {
            '/about': (_) => LinkRemoteAboutPage(
                  version: '1.0.43',
                  openSource: (uri) async {
                    opened.add(uri);
                    return true;
                  },
                )
          },
        )));
    expect(find.text('版本：1.0.43'), findsOneWidget);
    expect(find.textContaining('基于 RustDesk'), findsOneWidget);
    await _capture(tester, 'phone-light');
    tester.view.physicalSize = const Size(1024, 1366);
    await tester.pumpAndSettle();
    await _capture(tester, 'tablet-light');
    tester.view.physicalSize = const Size(390, 844);
    await tester.pumpAndSettle();
    await tester.tap(find.text('公开源码与版本'));
    await tester.pump();
    expect(opened, [Uri.parse(linkRemoteSourceUrl)]);
    // Flutter decompresses dependency NOTICES in real isolates. Allow that IO
    // to finish rather than advancing only the widget test's fake clock.
    await tester.runAsync(() async {
      await tester.tap(find.text('开源许可（可离线查看）'));
      await tester.pumpAndSettle();
    });
    expect(find.byType(LicensePage), findsOneWidget);
    expect(find.text('LinkRemote'), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets('about is scrollable on narrow large-text dark screens',
      (tester) async {
    tester.view.physicalSize = const Size(320, 568);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(RepaintBoundary(
        key: _captureKey,
        child: MaterialApp(
          debugShowCheckedModeBanner: false,
          theme: ThemeData.dark().copyWith(
            textTheme: _captureFont == null
                ? null
                : ThemeData.dark().textTheme.apply(fontFamily: 'AboutQa'),
          ),
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context)
                .copyWith(textScaler: const TextScaler.linear(1.3)),
            child: child!,
          ),
          home: const Scaffold(),
          initialRoute: '/about',
          routes: {
            '/about': (_) => const LinkRemoteAboutPage(version: '1.0.43')
          },
        )));
    await tester.ensureVisible(find.text('开源许可（可离线查看）'));
    await tester.pumpAndSettle();
    await _capture(tester, 'phone-dark-large-text');
    expect(tester.takeException(), isNull);
  });

  testWidgets('source failures are recoverable and safe after leaving page',
      (tester) async {
    final opening = Completer<bool>();
    await tester.pumpWidget(MaterialApp(
      home: LinkRemoteAboutPage(
        version: '1.0.43',
        openSource: (_) => opening.future,
      ),
    ));
    await tester.tap(find.text('公开源码与版本'));
    opening.complete(false);
    await tester.pumpAndSettle();
    expect(find.text('无法打开浏览器，请复制上方源码地址。'), findsOneWidget);

    final delayedFailure = Completer<bool>();
    await tester.pumpWidget(MaterialApp(
      home: LinkRemoteAboutPage(
        version: '1.0.43',
        openSource: (_) => delayedFailure.future,
      ),
    ));
    await tester.tap(find.text('公开源码与版本'));
    await tester.pumpWidget(const SizedBox.shrink());
    delayedFailure.completeError(StateError('no browser'));
    await tester.pump();
    expect(tester.takeException(), isNull);
  });
}
