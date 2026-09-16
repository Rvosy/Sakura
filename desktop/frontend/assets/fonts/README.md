# Runtime v2 Noto Sans fonts

These fonts are bundled for the Sakura Runtime v2 desktop frontend. They are not loaded from the
user's system or from the network.

## Transformation

- Source package: `Noto_Sans_JP,Noto_Sans_SC` downloaded from Google Fonts.
- Source files: the variable `NotoSansSC-VariableFont_wght.ttf` and
  `NotoSansJP-VariableFont_wght.ttf` files from that package.
- Weight axis: restricted to `400:400:700` (minimum/default/maximum).
- Character coverage: preserved in full; no Unicode or glyph subsetting was performed.
- Output format: WOFF2.
- Tools: fonttools 4.63.0 and brotli 1.2.0, installed in an isolated temporary directory.

| Asset | Bytes |
| --- | ---: |
| Source Noto Sans SC TTF | 17,773,248 |
| Source Noto Sans JP TTF | 9,135,128 |
| `NotoSansSC-VariableFont_wght-400-700.woff2` | 7,638,924 |
| `NotoSansJP-VariableFont_wght-400-700.woff2` | 3,992,740 |

The generated files retain every source glyph and cmap entry. See `OFL.txt` for redistribution
terms.
