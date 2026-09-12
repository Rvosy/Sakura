import { spine } from './vendor/spine-webgl.mjs';
import { createSpineController } from './controller.mjs';

export async function createRenderer({ container, rendererData, resolveAssetUrl, bindingId, resourceId,
  signal, onLayout = () => {}, onError = () => {} }) {
  const canvas = document.createElement('canvas');
  canvas.className = 'spine-canvas';
  canvas.setAttribute('aria-label', 'Spine 角色预览');
  Object.assign(canvas.style, { width: '100%', height: '100%', display: 'block' });
  const abort = new AbortController();
  const textures = new Map();
  const bitmaps = [];
  let shader, batcher, controller, observer, frame = 0, disposed = false, paused = false;
  let gl, lastTime, draw;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    abort.abort();
    signal?.removeEventListener('abort', dispose);
    observer?.disconnect();
    cancelAnimationFrame(frame);
    canvas.removeEventListener('webglcontextlost', contextLost);
    controller?.dispose();
    for (const texture of textures.values()) texture.dispose();
    for (const bitmap of bitmaps) bitmap.close();
    batcher?.dispose();
    shader?.dispose();
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
    canvas.remove();
  };
  function contextLost(event) {
    event.preventDefault();
    dispose();
    onError(new Error('SPINE_WEBGL_CONTEXT_LOST'));
  }
  function checkActive() {
    if (disposed || signal?.aborted) throw new DOMException('Spine loading cancelled', 'AbortError');
  }
  async function read(relative, method) {
    checkActive();
    const url = await resolveAssetUrl(relative);
    checkActive();
    const response = await fetch(url, { signal: abort.signal });
    if (!response.ok) throw new Error('SPINE_ASSET_LOAD_FAILED');
    return response[method]();
  }
  signal?.addEventListener('abort', dispose, { once: true });
  try {
    checkActive();
    if (!/^3\.6\.\d+$/.test(rendererData.runtimeVersion)) throw new Error('SPINE_VERSION_UNSUPPORTED');
    const { config } = rendererData;
    const [json, atlasText] = await Promise.all([read(config.skeleton, 'json'), read(config.atlas, 'text')]);
    checkActive();
    if (!/^3\.6\.\d+$/.test(json.skeleton?.spine)) throw new Error('SPINE_VERSION_UNSUPPORTED');
    gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: true, antialias: true });
    if (!gl) throw new Error('SPINE_WEBGL_UNAVAILABLE');
    canvas.addEventListener('webglcontextlost', contextLost);
    const context = new spine.webgl.ManagedWebGLRenderingContext(gl);
    for (const [page, path] of Object.entries(rendererData.textures)) {
      const blob = await read(path, 'blob');
      const bitmap = await createImageBitmap(blob, { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
      if (disposed || signal?.aborted) { bitmap.close(); checkActive(); }
      bitmaps.push(bitmap);
      if (Math.max(bitmap.width, bitmap.height) > gl.getParameter(gl.MAX_TEXTURE_SIZE)) throw new Error('SPINE_TEXTURE_TOO_LARGE');
      textures.set(page, new spine.webgl.GLTexture(context, bitmap));
    }
    const atlas = new spine.TextureAtlas(atlasText, page => {
      if (!textures.has(page)) throw new Error('SPINE_ATLAS_TEXTURE_MISSING');
      return textures.get(page);
    });
    const skeleton = new spine.Skeleton(new spine.SkeletonJson(new spine.AtlasAttachmentLoader(atlas)).readSkeletonData(json));
    controller = createSpineController(skeleton, config, { bindingId, resourceId });
    const offset = new spine.Vector2(), size = new spine.Vector2();
    skeleton.getBounds(offset, size, []);
    if (![offset.x, offset.y, size.x, size.y].every(Number.isFinite) || size.x <= 0 || size.y <= 0) throw new Error('SPINE_BOUNDS_INVALID');
    shader = spine.webgl.Shader.newTwoColoredTextured(context);
    batcher = new spine.webgl.PolygonBatcher(context);
    const painter = new spine.webgl.SkeletonRenderer(context);
    painter.premultipliedAlpha = config.premultipliedAlpha;
    const matrix = new spine.webgl.Matrix4();
    const centerX = offset.x + size.x / 2, centerY = offset.y + size.y / 2;
    function resize() {
      if (disposed) return;
      const width = Math.max(1, container.clientWidth), height = Math.max(1, container.clientHeight);
      const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * pixelRatio);
      canvas.height = Math.round(height * pixelRatio);
      const scale = Math.max(size.x / width, size.y / height) * 1.16;
      const worldWidth = width * scale, worldHeight = height * scale;
      matrix.ortho2d(centerX - worldWidth / 2, centerY - worldHeight / 2, worldWidth, worldHeight);
      gl.viewport(0, 0, canvas.width, canvas.height);
      onLayout({ width, height, bounds: { x: (width - size.x / scale) / 2, y: (height - size.y / scale) / 2,
        width: size.x / scale, height: size.y / scale } });
      draw?.();
    }
    draw = () => {
      if (disposed) return;
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      shader.bind();
      shader.setUniformi(spine.webgl.Shader.SAMPLER, 0);
      shader.setUniform4x4f(spine.webgl.Shader.MVP_MATRIX, matrix.values);
      batcher.begin(shader);
      painter.draw(batcher, skeleton);
      batcher.end();
      shader.unbind();
    };
    function tick(time) {
      if (disposed || paused) return;
      try {
        controller.update(lastTime === undefined ? 0 : (time - lastTime) / 1000);
        lastTime = time;
        draw();
        frame = requestAnimationFrame(tick);
      } catch (error) { dispose(); onError(error); }
    }
    container.append(canvas);
    observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();
    frame = requestAnimationFrame(tick);
    return {
      applyControl(control, delivery) { const applied = controller.applyControl(control, delivery); draw(); return applied; },
      cancel() { controller.cancel(); draw(); },
      resize,
      setPaused(value) {
        if (disposed || paused === Boolean(value)) return;
        paused = Boolean(value);
        cancelAnimationFrame(frame);
        lastTime = undefined;
        if (!paused) frame = requestAnimationFrame(tick);
      },
      snapshot: () => controller.snapshot(),
      dispose,
    };
  } catch (error) { dispose(); throw error; }
}
