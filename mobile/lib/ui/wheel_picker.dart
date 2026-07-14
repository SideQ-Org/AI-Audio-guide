// iOS-alarm-style scrollable wheel pickers used across the app (challenge goal/days,
// birthday). Brand-styled bottom sheet for dates + an inline number wheel for sheets.

import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';

import 'components.dart';
import 'design.dart';

Widget _grabber(AppColors c) => Container(
      width: 40, height: 4,
      margin: const EdgeInsets.only(top: 10, bottom: 14),
      decoration: BoxDecoration(color: c.textFaint.withValues(alpha: .4), borderRadius: BorderRadius.circular(2)),
    );

/// Scrollable date wheel (day · month · year) in a branded sheet — replaces the clunky
/// calendar dialog. Returns the picked date, or null on cancel.
Future<DateTime?> showAppDatePicker(
  BuildContext context, {
  DateTime? initial,
  DateTime? minDate,
  DateTime? maxDate,
  String? title,
}) {
  final now = DateTime.now();
  final min = minDate ?? DateTime(1910);
  final max = maxDate ?? now;
  var picked = initial ?? DateTime(now.year - 25, now.month, now.day);
  if (picked.isBefore(min)) picked = min;
  if (picked.isAfter(max)) picked = max;

  return showModalBottomSheet<DateTime>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    backgroundColor: Colors.transparent,
    builder: (ctx) {
      final c = ctx.colors;
      return CardSheet(
        child: Padding(
          padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).padding.bottom + 12),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            _grabber(c),
            if (title != null)
              Padding(padding: const EdgeInsets.only(bottom: 6), child: Text(title, style: h2(ctx))),
            SizedBox(
              height: 200,
              child: CupertinoTheme(
                data: CupertinoThemeData(
                  brightness: Theme.of(ctx).brightness,
                  textTheme: CupertinoTextThemeData(
                    dateTimePickerTextStyle:
                        body(ctx).copyWith(fontSize: 21, fontWeight: FontWeight.w700),
                  ),
                ),
                child: CupertinoDatePicker(
                  mode: CupertinoDatePickerMode.date,
                  initialDateTime: picked,
                  minimumDate: min,
                  maximumDate: max,
                  onDateTimeChanged: (d) => picked = d,
                ),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 10, 20, 6),
              child: Row(children: [
                Expanded(
                  child: AppButton(MaterialLocalizations.of(ctx).cancelButtonLabel,
                      kind: AppBtnKind.secondary, onTap: () => Navigator.pop(ctx)),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: AppButton(MaterialLocalizations.of(ctx).okButtonLabel,
                      onTap: () => Navigator.pop(ctx, picked)),
                ),
              ]),
            ),
          ]),
        ),
      );
    },
  );
}

/// Inline iOS-style number wheel (alarm-clock feel) for use inside sheets.
class NumberWheel extends StatefulWidget {
  const NumberWheel({
    super.key,
    required this.min,
    required this.max,
    required this.value,
    required this.onChanged,
    this.step = 1,
    this.suffix,
    this.label,
  });
  final int min;
  final int max;
  final int step;
  final int value;
  final ValueChanged<int> onChanged;
  final String? suffix;
  final String? label;

  @override
  State<NumberWheel> createState() => _NumberWheelState();
}

class _NumberWheelState extends State<NumberWheel> {
  late FixedExtentScrollController _ctrl;

  List<int> get _values => [for (int v = widget.min; v <= widget.max; v += widget.step) v];

  int _indexOf(int v) {
    final i = _values.indexOf(v);
    return i < 0 ? 0 : i;
  }

  @override
  void initState() {
    super.initState();
    _ctrl = FixedExtentScrollController(initialItem: _indexOf(widget.value));
  }

  @override
  void didUpdateWidget(covariant NumberWheel old) {
    super.didUpdateWidget(old);
    // Range or value changed externally (e.g. metric switched) → realign the wheel.
    final target = _indexOf(widget.value);
    if (_ctrl.hasClients && _ctrl.selectedItem != target) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_ctrl.hasClients) _ctrl.jumpToItem(target);
      });
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      if (widget.label != null) ...[
        Text(widget.label!, style: caption(context)),
        const SizedBox(height: 6),
      ],
      Container(
        height: 128,
        decoration: BoxDecoration(
          color: c.glassFill(0.05),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: c.glassBorder),
        ),
        child: CupertinoTheme(
          data: CupertinoThemeData(
            brightness: Theme.of(context).brightness,
            textTheme: CupertinoTextThemeData(
              pickerTextStyle:
                  titleS(context).copyWith(fontSize: 21, fontWeight: FontWeight.w800),
            ),
          ),
          child: CupertinoPicker(
            scrollController: _ctrl,
            itemExtent: 40,
            squeeze: 1.15,
            useMagnifier: true,
            magnification: 1.12,
            selectionOverlay: CupertinoPickerDefaultSelectionOverlay(
              background: c.primary.withValues(alpha: 0.08),
            ),
            onSelectedItemChanged: (i) => widget.onChanged(_values[i]),
            children: [
              for (final v in _values)
                Center(child: Text(widget.suffix != null ? '$v ${widget.suffix}' : '$v')),
            ],
          ),
        ),
      ),
    ]);
  }
}
