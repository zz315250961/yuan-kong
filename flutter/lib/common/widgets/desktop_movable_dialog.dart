import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter/gestures.dart';

/// Window-local dialog. Only its header initiates movement; content keeps its
/// own scroll gestures and the footer never scrolls out of reach.
class DesktopMovableDialog extends StatefulWidget {
  const DesktopMovableDialog(
      {super.key,
      required this.title,
      required this.content,
      required this.actions,
      required this.onCancel});
  final Widget title;
  final Widget content;
  final List<Widget> actions;
  final VoidCallback onCancel;
  @override
  State<DesktopMovableDialog> createState() => _DesktopMovableDialogState();
}

class _DesktopMovableDialogState extends State<DesktopMovableDialog> {
  final _focus =
      FocusScopeNode(traversalEdgeBehavior: TraversalEdgeBehavior.closedLoop);
  Offset? _position;
  @override
  void dispose() {
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, constraints) {
        final width = math.min(570.0, math.max(0.0, constraints.maxWidth - 32));
        final height =
            math.min(560.0, math.max(0.0, constraints.maxHeight - 32));
        final initial = _position ??
            Offset((constraints.maxWidth - width) / 2,
                (constraints.maxHeight - height) / 2);
        final position = Offset(
            initial.dx.clamp(0.0, constraints.maxWidth - width),
            initial.dy.clamp(0.0, constraints.maxHeight - height));
        return Stack(children: [
          Positioned(
              left: position.dx,
              top: position.dy,
              width: width,
              height: height,
              child: CallbackShortcuts(
                  bindings: {
                    const SingleActivator(LogicalKeyboardKey.escape):
                        widget.onCancel
                  },
                  child: FocusScope(
                      node: _focus,
                      autofocus: true,
                      child: FocusTraversalGroup(
                          child: Material(
                              elevation: 12,
                              color: Theme.of(context).colorScheme.surface,
                              borderRadius: BorderRadius.circular(14),
                              clipBehavior: Clip.antiAlias,
                              child: Column(children: [
                                MouseRegion(
                                    cursor: SystemMouseCursors.move,
                                    child: GestureDetector(
                                        behavior: HitTestBehavior.opaque,
                                        dragStartBehavior:
                                            DragStartBehavior.down,
                                        onPanUpdate: (event) => setState(() {
                                              final next =
                                                  (_position ?? position) +
                                                      event.delta;
                                              _position = Offset(
                                                  next.dx.clamp(
                                                      0.0,
                                                      constraints.maxWidth -
                                                          width),
                                                  next.dy.clamp(
                                                      0.0,
                                                      constraints.maxHeight -
                                                          height));
                                            }),
                                        child: Padding(
                                            padding: const EdgeInsets.fromLTRB(
                                                16, 8, 8, 8),
                                            child: Row(children: [
                                              const ExcludeSemantics(
                                                  child: Icon(
                                                      Icons.drag_indicator,
                                                      size: 20)),
                                              const SizedBox(width: 8),
                                              Expanded(
                                                  child: DefaultTextStyle.merge(
                                                      style: Theme.of(context)
                                                          .textTheme
                                                          .titleLarge,
                                                      child: widget.title)),
                                              IconButton(
                                                  tooltip: '关闭',
                                                  onPressed: widget.onCancel,
                                                  icon: const Icon(Icons.close))
                                            ])))),
                                const Divider(height: 1),
                                Expanded(
                                    child: Padding(
                                        padding: const EdgeInsets.all(16),
                                        child: widget.content)),
                                const Divider(height: 1),
                                Padding(
                                    padding: const EdgeInsets.all(12),
                                    child: OverflowBar(
                                        alignment: MainAxisAlignment.end,
                                        spacing: 8,
                                        overflowSpacing: 8,
                                        children: widget.actions)),
                              ]))))))
        ]);
      });
}
