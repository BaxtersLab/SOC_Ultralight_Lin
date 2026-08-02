# Retired: blank scroll-region templates

`agent1_scroll_up.png`, `agent5_scroll_dn.png`, `agent5_scroll_up.png`

These are captures of the *region* clicked to scroll, not of a distinguishable
button. They are effectively featureless:

| file | stdev | unique grey levels |
|---|---|---|
| agent5_scroll_dn.png | 0.0 | 1 (a single solid colour) |
| agent1_scroll_up.png | 0.3 | 2 |
| agent5_scroll_up.png | 4.2 | 3 |

`cv2.matchTemplate(..., TM_CCOEFF_NORMED)` scores a flat patch at ~1.0 against
ANY flat screen area, so they matched blank desktop at 0.89-1.00 — above the
0.80 TEMPLATE_THRESH. Measured on Ubuntu 26.04, 2026-08-02: three of five
apparent calibration "hits" were these false positives.

That is not harmless. `_apply_template_match` fills an empty slot:

    elif r == "scroll_up":
        if c.scroll_up_xy is None:      # only when unset
            c.scroll_up_xy = (px, py)

Manually set coordinates are protected, but on a FRESH calibration (fields
still None) a false match writes a bogus XY and SOC then clicks there. The
scroll action homes on the stored XY, so these images were never load-bearing
for matching — they just ended up in the template folder.

Platform-independent: this misfires on Windows too.

Moved here rather than deleted because this repo is not under version control
on the Linux box (git is not installed), so a delete would be unrecoverable.
`TEMPLATE_DIR.glob("*.png")` does not recurse, so nothing here is loaded.
Safe to delete permanently at any time.

## Added 2026-08-02 (second pass, after the case-sensitivity fix)

`Agent1_chat_input_field.PNG` — 217x40, stdev 4.4. A capture of an EMPTY input
field, so almost entirely flat background. Matched blank screen at conf 0.915.

Only surfaced once `template_pngs()` made uppercase `.PNG` files visible; before
that this template was never loaded on Linux at all. Lower severity than the
scroll blanks: its role (`chat_input_field`) is not one of the four core roles
(input/send/scroll_dn/scroll_up), so it pollutes the training registry but does
not populate a coordinate slot.
