# Telegram Migration Pipeline — identity usage

> High-throughput, crash-resilient migration and extraction engine for Telegram channels with MTProto batching and adaptive backoff.
> Audience: Community managers and sysadmins migrating channel archives

## Concept
A message bubble releasing a stream of payloads to the right — messages migrating out, checkpointed and resumable.

The compact mark carries the identity alone; the primary lockup pairs it with the
'Telegram Migration Pipeline' wordmark (Space Grotesk SemiBold, converted to outlines — no font
required to render the production files).
**Ownership / provenance:** No Telegram trademarks are used: the mark is an original message-bubble/stream construction, not the Telegram paper-plane.


## Colour
| Role | Light backgrounds | Dark backgrounds |
|---|---|---|
| Structure / ink | `#0B1220` | `#F8FAFC` |
| Accent | `#0284C7` | `#38BDF8` |

Single-colour reproduction: use `*-mono.svg` (all geometry set to
`currentColor`); set any colour ≥ 4.5:1 contrast against the background.
Negative space (needle, slashes, core, route) must always show the background —
never fill it with a second colour.

## Typography
Wordmark: **Space Grotesk SemiBold (600)**, SIL Open Font License 1.1
(see `../FONT-LICENSE.txt`). In delivered SVGs the letterforms are outlined
vector paths, so no font installation is ever required. Do not re-typeset the
name in another face or alter casing/spelling ('Telegram Migration Pipeline').

## Size & clear space
- Compact mark: minimum **16 px**; verified legible at 16/32/64 px.
- Primary lockup: minimum **24 px** height (≈ 9 mm print).
- Clear space: **¼ of the mark height** on all sides of any lockup or mark.
- App icon / avatar: use `telegram-migration-pipeline-appicon.svg` (tile `#0B1220`, radius 56/256).

## Misuse
Do not rotate, skew, add gradients/shadows/outlines, recolor outside the
palette, place the light variant on mid-tone backgrounds, or reconstruct the
mark from live text.

## Files
- `svg/telegram-migration-pipeline-primary.svg` · `-primary-dark.svg` · `-primary-mono.svg`
- `svg/telegram-migration-pipeline-mark.svg` · `-mark-dark.svg` · `-mark-mono.svg` · `-appicon.svg`
- `png/telegram-migration-pipeline-mark-1024|512|256|64|32|16.png`, `telegram-migration-pipeline-mark-dark-1024.png`,
  `telegram-migration-pipeline-primary-light-1600.png`, `telegram-migration-pipeline-primary-dark-1600.png`,
  `telegram-migration-pipeline-appicon-512.png`
