// Samples the same clipped triangles submitted to the GPU. No framebuffer readback.
export function sampleAlpha(mask, u, v) {
  const x = Math.max(0, Math.min(mask.width - 1, u * mask.width - 0.5));
  const y = Math.max(0, Math.min(mask.height - 1, v * mask.height - 0.5));
  const x0 = Math.floor(x), y0 = Math.floor(y);
  const x1 = Math.min(x0 + 1, mask.width - 1), y1 = Math.min(y0 + 1, mask.height - 1);
  const fx = x - x0, fy = y - y0, a = mask.alpha;
  return ((a[y0 * mask.width + x0] * (1 - fx) + a[y0 * mask.width + x1] * fx) * (1 - fy)
    + (a[y1 * mask.width + x0] * (1 - fx) + a[y1 * mask.width + x1] * fx) * fy) / 255;
}

export function sampleTriangles(mask, vertices, triangles, x, y, alpha = 0, source = 1, destination = 771) {
  // SkeletonRenderer uses two-color vertices: x, y, rgba, uv, dark-rgba.
  const factor = (mode, src, dst) => {
    if (mode === 0) return 0;
    if (mode === 770 || mode === 768) return src;
    if (mode === 771 || mode === 769) return 1 - src;
    if (mode === 772 || mode === 774) return dst;
    if (mode === 773 || mode === 775) return 1 - dst;
    return 1;
  };
  for (let i = 0; i < triangles.length; i += 3) {
    const a = triangles[i] * 12, b = triangles[i + 1] * 12, c = triangles[i + 2] * 12;
    const ax = vertices[a], ay = vertices[a + 1], bx = vertices[b], by = vertices[b + 1], cx = vertices[c], cy = vertices[c + 1];
    if (x < Math.min(ax, bx, cx) || x > Math.max(ax, bx, cx) || y < Math.min(ay, by, cy) || y > Math.max(ay, by, cy)) continue;
    const det = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
    if (Math.abs(det) < 1e-10) continue;
    const wa = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / det;
    const wb = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / det;
    const wc = 1 - wa - wb;
    if (wa < -1e-7 || wb < -1e-7 || wc < -1e-7) continue;
    const ownsEdge = (a, b) => {
      if (det < 0) [a, b] = [b, a];
      return vertices[a + 1] < vertices[b + 1]
        || (vertices[a + 1] === vertices[b + 1] && vertices[a] > vertices[b]);
    };
    // Half-open edges prevent double blending at a shared edge, while allowing
    // genuinely overlapping/deformed triangles to composite independently.
    if ((Math.abs(wa) < 1e-7 && !ownsEdge(b, c)) || (Math.abs(wb) < 1e-7 && !ownsEdge(c, a))
      || (Math.abs(wc) < 1e-7 && !ownsEdge(a, b))) continue;
    const src = sampleAlpha(mask, vertices[a + 6] * wa + vertices[b + 6] * wb + vertices[c + 6] * wc,
      vertices[a + 7] * wa + vertices[b + 7] * wb + vertices[c + 7] * wc)
      * (vertices[a + 5] * wa + vertices[b + 5] * wb + vertices[c + 5] * wc);
    alpha = Math.min(1, src * factor(source, src, alpha) + alpha * factor(destination, src, alpha));
  }
  return alpha;
}

export function readTextureAlpha(bitmap) {
  const canvas = document.createElement('canvas');
  canvas.width = bitmap.width; canvas.height = bitmap.height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  context.drawImage(bitmap, 0, 0);
  const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
  const alpha = new Uint8Array(bitmap.width * bitmap.height);
  for (let i = 0; i < alpha.length; i++) alpha[i] = rgba[i * 4 + 3];
  canvas.width = canvas.height = 1;
  return { width: bitmap.width, height: bitmap.height, alpha };
}
