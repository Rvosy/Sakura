import test from "node:test";
import assert from "node:assert/strict";
import { themeVariables, readThemeColors, colorEditsToLegacy } from "./theme-model.js";
import { FALLBACK_THEME_TOKENS } from "../../core/theme.js";
import { toLegacyThemeTokens } from "../../core/theme-runtime.js";

test("modern and legacy saved themes populate the same reduced color editor", () => {
  const theme = {...FALLBACK_THEME_TOKENS,primary:"#112233",primaryHover:"#223344",accent:"#445566"};
  assert.deepEqual(readThemeColors(theme), {accent:"#112233",hover:"#223344",highlight:"#445566"});
  assert.deepEqual(readThemeColors(toLegacyThemeTokens(theme)), readThemeColors(theme));
});

test("editing reduced colors retains every other saved legacy field", () => {
  const before = toLegacyThemeTokens({...FALLBACK_THEME_TOKENS,pageBackground:"#123456",text:"#abcdef"});
  const patch = colorEditsToLegacy({accent:"AA2233",hover:"#CC4455",highlight:"#EE6677",mode:"dark"});
  const after = {...before,...patch};
  assert.deepEqual(patch,{primary_color:"#aa2233",primary_hover_color:"#cc4455",accent_color:"#ee6677"});
  for (const key of Object.keys(before).filter(key => !(key in patch))) assert.equal(after[key],before[key]);
  assert.deepEqual(readThemeColors(after),{accent:"#aa2233",hover:"#cc4455",highlight:"#ee6677"});
  assert.deepEqual(colorEditsToLegacy({accent:"invalid",hover:"#123",highlight:""}),{});
});

test("changing the character accent leaves the interface surfaces and text unchanged", () => {
  for (const mode of ["light", "dark"]) {
    const {accent: blue, ...before} = themeVariables(mode, "#4b9ac4");
    const {accent: pink, ...after} = themeVariables(mode, "#cd6888");
    assert.notEqual(blue, pink);
    assert.deepEqual(before, after);
  }
});
test("system mode follows the operating system without overriding an explicit choice", () => {
  assert.deepEqual(themeVariables("system", "#629679", true), themeVariables("dark", "#629679"));
  assert.deepEqual(themeVariables("system", "#629679", false), themeVariables("light", "#629679"));
  assert.deepEqual(themeVariables("light", "#629679", true), themeVariables("light", "#629679", false));
});
