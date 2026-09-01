import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_hbb/models/model.dart';

void main() {
  test('quality monitor parses adaptive video fields', () {
    final data = QualityMonitorData();
    data.applyEvent({
      'target_fps': '45',
      'qos_tier': 'congested',
      'capture_scale': 'half',
      'transport': 'relay',
    });

    expect(data.targetFps, '45');
    expect(data.qosTier, 'congested');
    expect(data.captureScale, '1/2');
    expect(data.transport, 'relay');
  });
}
