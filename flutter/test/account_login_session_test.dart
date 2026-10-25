import 'package:flutter_hbb/models/account_login_session.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('session summary keeps opaque identity and unknown legacy metadata', () {
    final session = AccountLoginSession.fromJson({
      'id': 'synthetic-session-opaque-01',
      'device_name': '',
      'platform': '',
      'created_at': '2026-10-25T00:00:00.000Z',
      'last_seen_at': '',
      'is_current': true,
      'token': 'must-not-be-retained',
    });
    expect(session.id, 'synthetic-session-opaque-01');
    expect(session.deviceName, '');
    expect(session.lastSeenAt, isNull);
    expect(session.isCurrent, isTrue);
    expect(session.toString(), isNot(contains('must-not-be-retained')));
  });
  test('malformed identity or current marker must not create actionable rows',
      () {
    for (final body in <Map<String, dynamic>>[
      {},
      {'id': '../other', 'is_current': false},
      {'id': 'synthetic-session-opaque-01', 'is_current': 'true'},
    ]) {
      expect(() => AccountLoginSession.fromJson(body), throwsFormatException);
    }
  });
}
