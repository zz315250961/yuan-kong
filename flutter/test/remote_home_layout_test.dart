import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_hbb/common.dart' show MyTheme;
import 'package:flutter_hbb/common/widgets/remote_home_layout.dart';
import 'package:flutter_test/flutter_test.dart';

const captureKey = ValueKey('capture');
void main() {
  final captureDir = Platform.environment['LINKREMOTE_HOME_CAPTURE_DIR'];
  final font = Platform.environment['LINKREMOTE_HOME_CAPTURE_FONT'];
  setUpAll(() async {
    if (font == null) return;
    final loader = FontLoader('HomeQa')
      ..addFont(
          Future.value(ByteData.sublistView(File(font).readAsBytesSync())));
    await loader.load();
    await (FontLoader('MaterialIcons')
          ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
        .load();
  });
  for (final size in [
    const Size(700, 600),
    const Size(1000, 720),
    const Size(1440, 900)
  ]) {
    for (final dark in [false, true]) {
      testWidgets('connection actions fit $size dark=$dark at 1.3 text',
          (tester) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        var connected = 0;
        var devices = 0;
        await tester.pumpWidget(RepaintBoundary(
            key: captureKey,
            child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: (dark ? MyTheme.darkTheme : MyTheme.lightTheme).copyWith(
                  textTheme: (dark ? MyTheme.darkTheme : MyTheme.lightTheme)
                      .textTheme
                      .apply(fontFamily: font == null ? null : 'HomeQa')),
              home: MediaQuery(
                data: MediaQueryData(
                    size: size, textScaler: const TextScaler.linear(1.3)),
                child: Scaffold(
                    body: RemoteHomeLayout(
                        localPane: SizedBox(
                            width: 240,
                            child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  const Padding(
                                      padding: EdgeInsets.all(20),
                                      child: Text('Local device · synthetic')),
                                  LocalIdentityGroup(
                                      identity: const Padding(
                                          padding: EdgeInsets.all(16),
                                          child: Text('ID\n000 000 000')),
                                      security: const Padding(
                                          padding: EdgeInsets.all(16),
                                          child: Text(
                                              'One-time password\n••••••\n\nPermanent password\nNot set'))),
                                ])),
                        connectionPane: ConnectionWorkspace(
                          status: const SizedBox(
                              height: 48,
                              child: Align(
                                  alignment: Alignment.centerLeft,
                                  child: Text('  ● Ready · synthetic'))),
                          peers: ListView(children: [
                            const Padding(
                                padding: EdgeInsets.symmetric(vertical: 16),
                                child: Text('Recent sessions · synthetic')),
                            Card(
                                child: Padding(
                                    padding: const EdgeInsets.all(12),
                                    child: DeviceDetailsLayout(
                                      icon: const Icon(Icons.computer),
                                      details: const Column(
                                          crossAxisAlignment:
                                              CrossAxisAlignment.start,
                                          children: [
                                            Text(
                                                'Synthetic studio workstation with a long name',
                                                maxLines: 2,
                                                overflow:
                                                    TextOverflow.ellipsis),
                                            Text('000 000 001', maxLines: 1),
                                            Text('Windows · synthetic'),
                                          ]),
                                      connectAction: IconButton(
                                          onPressed: () {},
                                          icon: const Icon(
                                              Icons.settings_remote)),
                                      moreAction: IconButton(
                                          onPressed: () {},
                                          icon: const Icon(Icons.more_vert)),
                                    ))),
                          ]),
                          form: Align(
                              alignment: Alignment.centerLeft,
                              child: RemoteConnectionPanel(
                                title: const Text('Control remote desktop',
                                    style: TextStyle(fontSize: 20)),
                                devicesLabel: 'My devices',
                                onDevices: () => devices++,
                                input: const TextField(
                                    decoration: InputDecoration(
                                        hintText: 'Synthetic remote ID')),
                                connectLabel: 'Connect',
                                onConnect: () => connected++,
                                menu: IconButton(
                                    onPressed: () {},
                                    icon: const Icon(Icons.more_horiz)),
                              )),
                        ))),
              ),
            )));
        expect(tester.takeException(), isNull);
        expect(find.text('My devices'), findsOneWidget);
        expect(tester.getSize(find.byType(ElevatedButton)).height,
            greaterThanOrEqualTo(44));
        await tester.tap(find.text('Connect'));
        await tester.tap(find.text('My devices'));
        expect(connected, 1);
        expect(devices, 1);
        if (captureDir != null) {
          await tester.pump();
          await tester.runAsync(() async {
            final image = await tester
                .renderObject<RenderRepaintBoundary>(find.byKey(captureKey))
                .toImage();
            final data = await image.toByteData(format: ui.ImageByteFormat.png);
            final file = File(
                '$captureDir/home-${size.width.toInt()}-${dark ? 'dark' : 'light'}.png');
            file.parent.createSync(recursive: true);
            file.writeAsBytesSync(data!.buffer.asUint8List());
            image.dispose();
          });
        }
      });
    }
  }
  for (final incomingOnly in [true, false]) {
    testWidgets(
        '${incomingOnly ? 'incoming' : 'outgoing'} only preserves reachable pane actions',
        (tester) async {
      var selected = 0;
      await tester.pumpWidget(MaterialApp(
          home: Scaffold(
              body: RemoteHomeLayout(
        localPane: SizedBox(
            width: incomingOnly ? 280 : 240,
            child: TextButton(
                onPressed: () => selected++, child: const Text('Settings'))),
        connectionPane: incomingOnly
            ? null
            : ConnectionWorkspace(
                form: TextButton(
                    onPressed: () => selected++, child: const Text('Connect')),
                peers: const Text('Recent sessions'),
              ),
      ))));
      expect(tester.takeException(), isNull);
      expect(
          find.text('Connect'), incomingOnly ? findsNothing : findsOneWidget);
      await tester.tap(find.text('Settings'));
      if (!incomingOnly) await tester.tap(find.text('Connect'));
      expect(selected, incomingOnly ? 1 : 2);
    });
  }
  testWidgets('long device names preserve 48px actions at 280px',
      (tester) async {
    var connects = 0;
    var more = 0;
    await tester.pumpWidget(MaterialApp(
        theme: ThemeData(visualDensity: VisualDensity.compact),
        home: Scaffold(
            body: Align(
          alignment: Alignment.topLeft,
          child: SizedBox(
              width: 280,
              child: MediaQuery(
                data: const MediaQueryData(textScaler: TextScaler.linear(1.3)),
                child: DeviceDetailsLayout(
                  icon: const Icon(Icons.computer),
                  details: const Text(
                      'Synthetic workstation with a very long device name',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis),
                  connectAction: TextButton(
                      onPressed: () => connects++,
                      child: const Text('Connect')),
                  moreAction: IconButton(
                      onPressed: () => more++,
                      icon: const Icon(Icons.more_vert)),
                ),
              )),
        ))));
    expect(tester.takeException(), isNull);
    expect(tester.getSize(find.byType(TextButton)).height,
        greaterThanOrEqualTo(48));
    expect(tester.getSize(find.byType(IconButton)).height,
        greaterThanOrEqualTo(48));
    await tester.tap(find.text('Connect'));
    await tester.tap(find.byIcon(Icons.more_vert));
    expect(connects, 1);
    expect(more, 1);
  });
}
