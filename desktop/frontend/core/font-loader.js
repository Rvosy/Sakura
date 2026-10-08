const FONT_REQUESTS = Object.freeze({
  sc: Object.freeze({
    descriptor: '400 1em "Sakura Noto Sans SC"',
    sample: "中文 Sakura",
  }),
  jp: Object.freeze({
    descriptor: '400 1em "Sakura Noto Sans JP"',
    sample: "日本語 Sakura",
  }),
});

function applyFontState(documentRef, status) {
  const root = documentRef?.documentElement;
  if (root?.dataset) {
    root.dataset.runtimeFonts = status === "loaded" ? "loaded" : "fallback";
  }
}

export async function waitForRuntimeFonts({
  documentRef = globalThis.document,
  families = ["sc", "jp"],
} = {}) {
  const fontSet = documentRef?.fonts;
  if (!fontSet || typeof fontSet.load !== "function") {
    applyFontState(documentRef, "unsupported");
    return "unsupported";
  }

  const requests = families.map((family) => {
    const request = FONT_REQUESTS[family];
    if (!request) throw new TypeError(`Unknown runtime font family: ${family}`);
    return request;
  });

  const status = await Promise.all(
    requests.map(({ descriptor, sample }) => fontSet.load(descriptor, sample)),
  ).then(async (loadedFaces) => {
    if (loadedFaces.some((faces) => !faces || faces.length === 0)) return "fallback";
    if (fontSet.ready) await fontSet.ready;
    return "loaded";
  }).catch(() => "fallback");

  applyFontState(documentRef, status);
  return status;
}
