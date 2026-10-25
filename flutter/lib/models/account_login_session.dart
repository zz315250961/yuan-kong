/// Display-only summary. Never contains the credential used by this session.
class AccountLoginSession {
  const AccountLoginSession(
      {required this.id,
      required this.deviceName,
      required this.platform,
      required this.isCurrent,
      this.createdAt,
      this.lastSeenAt});
  final String id;
  final String deviceName;
  final String platform;
  final bool isCurrent;
  final DateTime? createdAt;
  final DateTime? lastSeenAt;

  factory AccountLoginSession.fromJson(Map<String, dynamic> json) {
    final id = json['id'];
    if (id is! String ||
        !RegExp(r'^[A-Za-z0-9_-]{22,64}$').hasMatch(id) ||
        json['is_current'] is! bool) {
      throw const FormatException('登录会话数据无效');
    }
    String text(String key, int limit) => json[key] is String
        ? String.fromCharCodes((json[key] as String).runes.take(limit))
        : '';
    DateTime? date(String key) =>
        json[key] is String ? DateTime.tryParse(json[key] as String) : null;
    return AccountLoginSession(
        id: id,
        deviceName: text('device_name', 80),
        platform: text('platform', 32),
        isCurrent: json['is_current'] as bool,
        createdAt: date('created_at'),
        lastSeenAt: date('last_seen_at'));
  }
}
