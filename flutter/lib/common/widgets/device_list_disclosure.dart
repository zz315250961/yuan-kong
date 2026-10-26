import 'dart:math' as math;
import 'package:flutter/material.dart';

/// Shared bounded presentation; it owns no records, credentials or row actions.
class DeviceListDisclosure extends StatefulWidget {
  const DeviceListDisclosure(
      {super.key,
      required this.count,
      required this.builder,
      this.unit = '条',
      this.resetKey});
  final int count;
  final String unit;
  final Object? resetKey;
  final Widget Function(BuildContext context, int visibleCount) builder;

  @override
  State<DeviceListDisclosure> createState() => _DeviceListDisclosureState();
}

class _DeviceListDisclosureState extends State<DeviceListDisclosure> {
  bool _expanded = false;
  static const _limit = 5;

  @override
  void didUpdateWidget(covariant DeviceListDisclosure oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.resetKey != widget.resetKey ||
        oldWidget.count != widget.count) {
      _expanded = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final visible = _expanded ? widget.count : math.min(_limit, widget.count);
    return Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            Expanded(
                child: Text(
                    '共${widget.count}${widget.unit}，显示$visible${widget.unit}',
                    style: Theme.of(context).textTheme.bodySmall)),
            if (widget.count > _limit)
              TextButton(
                  onPressed: () => setState(() => _expanded = !_expanded),
                  child: Text(_expanded
                      ? '收起'
                      : '展开全部（${widget.count}${widget.unit}）')),
          ]),
          widget.builder(context, visible),
        ]);
  }
}
