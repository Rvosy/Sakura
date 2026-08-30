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

| Asset | SHA-256 | Bytes |
| --- | --- | ---: |
| Source Noto Sans SC TTF | `E80613A35583F59B46DBF6CC2EB640F3DB0BB0F53FA7F6FBAA7B09FAF20E5172` | 17,773,248 |
| Source Noto Sans JP TTF | `04B2AC921347B12C63BC35ADDA5722DD2B1860D900F668DC5050A44202464FA5` | 9,135,128 |
| `NotoSansSC-VariableFont_wght-400-700.woff2` | `BFD25C99C6327B5525844F3301F4C487F0FC23347A02EE2B4DB17B2038A74686` | 7,638,924 |
| `NotoSansJP-VariableFont_wght-400-700.woff2` | `821DA781235AD4B06380463D0679106E515D4E3487D6A8DD90611058B1A61268` | 3,992,740 |

The generated files retain every source glyph and cmap entry. See `OFL.txt` for redistribution
terms.
