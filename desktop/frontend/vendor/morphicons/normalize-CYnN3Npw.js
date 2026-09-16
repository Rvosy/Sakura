//#region src/core/parse.ts
const COMMANDS = "MmLlHhVvCcSsQqTtAaZz";
function parsePath(d) {
	const subs = [];
	const n = d.length;
	let i = 0;
	let cx = 0;
	let cy = 0;
	let sx = 0;
	let sy = 0;
	let cur = null;
	let cmd = "";
	let px = 0;
	let py = 0;
	let prev = "";
	let started = false;
	const err = (msg) => {
		throw new Error(`morphicons: ${msg} at d[${i}]`);
	};
	const isDigit = (c) => c >= 48 && c <= 57;
	const skip = () => {
		while (i < n) {
			const c = d.charCodeAt(i);
			if (c === 32 || c === 9 || c === 10 || c === 13 || c === 12 || c === 44) i++;
			else break;
		}
	};
	const num = () => {
		skip();
		const start = i;
		if (i < n && (d[i] === "+" || d[i] === "-")) i++;
		let dig = false;
		while (i < n && isDigit(d.charCodeAt(i))) {
			i++;
			dig = true;
		}
		if (i < n && d[i] === ".") {
			i++;
			while (i < n && isDigit(d.charCodeAt(i))) {
				i++;
				dig = true;
			}
		}
		if (!dig) err("expected number");
		if (i < n && (d[i] === "e" || d[i] === "E")) {
			const save = i;
			i++;
			if (i < n && (d[i] === "+" || d[i] === "-")) i++;
			let ed = false;
			while (i < n && isDigit(d.charCodeAt(i))) {
				i++;
				ed = true;
			}
			if (!ed) i = save;
		}
		return Number(d.slice(start, i));
	};
	const flag = () => {
		skip();
		const c = d[i];
		if (c === "0" || c === "1") {
			i++;
			return c === "1" ? 1 : 0;
		}
		return err("expected arc flag (0|1)");
	};
	const open = () => {
		if (!started) err("path must start with M/m");
		if (!cur) {
			cur = {
				x0: cx,
				y0: cy,
				segs: [],
				closed: false
			};
			subs.push(cur);
		}
		return cur;
	};
	let rel = false;
	const nx = () => num() + (rel ? cx : 0);
	const ny = () => num() + (rel ? cy : 0);
	while (true) {
		skip();
		if (i >= n) break;
		const ch = d[i];
		if (COMMANDS.includes(ch)) {
			cmd = ch;
			i++;
		} else if (cmd === "") err("path must start with M/m");
		else if (cmd === "M") cmd = "L";
		else if (cmd === "m") cmd = "l";
		else if (cmd === "Z" || cmd === "z") err("stray data after Z");
		rel = cmd >= "a";
		switch (rel ? cmd.toUpperCase() : cmd) {
			case "M": {
				started = true;
				const x = nx();
				const y = ny();
				cx = x;
				cy = y;
				sx = x;
				sy = y;
				cur = {
					x0: x,
					y0: y,
					segs: [],
					closed: false
				};
				subs.push(cur);
				prev = "";
				break;
			}
			case "L": {
				const x = nx();
				const y = ny();
				open().segs.push([
					"L",
					x,
					y
				]);
				cx = x;
				cy = y;
				prev = "";
				break;
			}
			case "H": {
				const x = nx();
				open().segs.push([
					"L",
					x,
					cy
				]);
				cx = x;
				prev = "";
				break;
			}
			case "V": {
				const y = ny();
				open().segs.push([
					"L",
					cx,
					y
				]);
				cy = y;
				prev = "";
				break;
			}
			case "C":
			case "S": {
				let x1;
				let y1;
				if (cmd === "C" || cmd === "c") {
					x1 = nx();
					y1 = ny();
				} else {
					x1 = prev === "C" ? 2 * cx - px : cx;
					y1 = prev === "C" ? 2 * cy - py : cy;
				}
				const x2 = nx();
				const y2 = ny();
				const x = nx();
				const y = ny();
				open().segs.push([
					"C",
					x1,
					y1,
					x2,
					y2,
					x,
					y
				]);
				px = x2;
				py = y2;
				cx = x;
				cy = y;
				prev = "C";
				break;
			}
			case "Q":
			case "T": {
				let x1;
				let y1;
				if (cmd === "Q" || cmd === "q") {
					x1 = nx();
					y1 = ny();
				} else {
					x1 = prev === "Q" ? 2 * cx - px : cx;
					y1 = prev === "Q" ? 2 * cy - py : cy;
				}
				const x = nx();
				const y = ny();
				open().segs.push([
					"Q",
					x1,
					y1,
					x,
					y
				]);
				px = x1;
				py = y1;
				cx = x;
				cy = y;
				prev = "Q";
				break;
			}
			case "A": {
				const rx = num();
				const ry = num();
				const rot = num();
				const large = flag();
				const sweep = flag();
				const x = nx();
				const y = ny();
				open().segs.push([
					"A",
					rx,
					ry,
					rot,
					large,
					sweep,
					x,
					y
				]);
				cx = x;
				cy = y;
				prev = "";
				break;
			}
			case "Z":
				if (cur) {
					cur.closed = true;
					cur = null;
				}
				cx = sx;
				cy = sy;
				prev = "";
				break;
			default: err(`unsupported command "${cmd}"`);
		}
	}
	return subs.filter((s) => s.segs.length > 0);
}
//#endregion
//#region src/core/serialize.ts
function fmt(v) {
	return String(Math.round(v * 100) / 100);
}
/** Sampled subpaths → polyline `d` attribute. `closed[k]` appends Z to
*  subpath k (closed loops in flight); without flags everything is open. */
function serialize(subs, closed) {
	let d = "";
	for (let k = 0; k < subs.length; k++) {
		const o = subs[k];
		const n = o.length / 2;
		d += `M${fmt(o[0])} ${fmt(o[1])}`;
		for (let i = 1; i < n; i++) d += `L${fmt(o[2 * i])} ${fmt(o[2 * i + 1])}`;
		if (closed?.[k]) d += "Z";
	}
	return d;
}
function fmtCanon(v) {
	return String(Math.round(v * 1e4) / 1e4);
}
/** Cubic subpaths → canonical `d`, quantized to 4 decimals (engine-stable
*  bytes; see fmtCanon). */
function cubicsToPathD(paths) {
	let d = "";
	for (const { pts, closed } of paths) {
		d += `M${fmtCanon(pts[0])} ${fmtCanon(pts[1])}`;
		for (let i = 2; i < pts.length; i += 6) d += `C${fmtCanon(pts[i])} ${fmtCanon(pts[i + 1])} ${fmtCanon(pts[i + 2])} ${fmtCanon(pts[i + 3])} ${fmtCanon(pts[i + 4])} ${fmtCanon(pts[i + 5])}`;
		if (closed) d += "Z";
	}
	return d;
}
//#endregion
//#region src/core/normalize.ts
/** Control-point offset for a quarter circle: (4/3)·tan(π/8) ≈ 0.5523. */
const KAPPA = 4 / 3 * Math.tan(Math.PI / 8);
const TAU = 2 * Math.PI;
function builder(x0, y0) {
	const pts = [x0, y0];
	let cx = x0;
	let cy = y0;
	const cubic = (x1, y1, x2, y2, x, y) => {
		pts.push(x1, y1, x2, y2, x, y);
		cx = x;
		cy = y;
	};
	const line = (x, y) => {
		if (Math.abs(x - cx) < 1e-12 && Math.abs(y - cy) < 1e-12) return;
		cubic(cx + (x - cx) / 3, cy + (y - cy) / 3, cx + 2 * (x - cx) / 3, cy + 2 * (y - cy) / 3, x, y);
	};
	const quad = (x1, y1, x, y) => {
		cubic(cx + 2 / 3 * (x1 - cx), cy + 2 / 3 * (y1 - cy), x + 2 / 3 * (x1 - x), y + 2 / 3 * (y1 - y), x, y);
	};
	const arc = (rx0, ry0, rotDeg, large, sweep, x, y) => {
		const x1 = cx;
		const y1 = cy;
		if (Math.abs(x - x1) < 1e-12 && Math.abs(y - y1) < 1e-12) return;
		let rx = Math.abs(rx0);
		let ry = Math.abs(ry0);
		if (rx < 1e-12 || ry < 1e-12) {
			line(x, y);
			return;
		}
		const phi = rotDeg * Math.PI / 180;
		const cosP = Math.cos(phi);
		const sinP = Math.sin(phi);
		const hx = (x1 - x) / 2;
		const hy = (y1 - y) / 2;
		const x1p = cosP * hx + sinP * hy;
		const y1p = -sinP * hx + cosP * hy;
		const lam = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry);
		if (lam > 1) {
			const s = Math.sqrt(lam);
			rx *= s;
			ry *= s;
		}
		const rx2 = rx * rx;
		const ry2 = ry * ry;
		const xp2 = x1p * x1p;
		const yp2 = y1p * y1p;
		let rad = (rx2 * ry2 - rx2 * yp2 - ry2 * xp2) / (rx2 * yp2 + ry2 * xp2);
		if (rad < 0) rad = 0;
		const co = (large === sweep ? -1 : 1) * Math.sqrt(rad);
		const cxp = co * rx * y1p / ry;
		const cyp = -co * ry * x1p / rx;
		const ccx = cosP * cxp - sinP * cyp + (x1 + x) / 2;
		const ccy = sinP * cxp + cosP * cyp + (y1 + y) / 2;
		const th1 = Math.atan2((y1p - cyp) / ry, (x1p - cxp) / rx);
		let dth = Math.atan2((-y1p - cyp) / ry, (-x1p - cxp) / rx) - th1;
		if (sweep === 0 && dth > 0) dth -= TAU;
		else if (sweep === 1 && dth < 0) dth += TAU;
		const slices = Math.max(1, Math.ceil(Math.abs(dth) / (Math.PI / 2) - 1e-9));
		const delta = dth / slices;
		const alpha = 4 / 3 * Math.tan(delta / 4);
		const ex = (t) => ccx + rx * Math.cos(t) * cosP - ry * Math.sin(t) * sinP;
		const ey = (t) => ccy + rx * Math.cos(t) * sinP + ry * Math.sin(t) * cosP;
		const dx = (t) => -rx * Math.sin(t) * cosP - ry * Math.cos(t) * sinP;
		const dy = (t) => -rx * Math.sin(t) * sinP + ry * Math.cos(t) * cosP;
		let t0 = th1;
		let p0x = x1;
		let p0y = y1;
		for (let s = 1; s <= slices; s++) {
			const t1 = th1 + delta * s;
			const p1x = s === slices ? x : ex(t1);
			const p1y = s === slices ? y : ey(t1);
			cubic(p0x + alpha * dx(t0), p0y + alpha * dy(t0), p1x - alpha * dx(t1), p1y - alpha * dy(t1), p1x, p1y);
			t0 = t1;
			p0x = p1x;
			p0y = p1y;
		}
	};
	const finish = (closed) => {
		if (closed) line(pts[0], pts[1]);
		if (pts.length < 8) return null;
		return {
			pts: Float64Array.from(pts),
			closed
		};
	};
	return [
		cubic,
		line,
		quad,
		arc,
		finish
	];
}
function lowerSubpath(raw) {
	const [cubic, line, quad, arc, finish] = builder(raw.x0, raw.y0);
	for (const s of raw.segs) switch (s[0]) {
		case "L":
			line(s[1], s[2]);
			break;
		case "C":
			cubic(s[1], s[2], s[3], s[4], s[5], s[6]);
			break;
		case "Q":
			quad(s[1], s[2], s[3], s[4]);
			break;
		case "A": arc(s[1], s[2], s[3], s[4], s[5], s[6], s[7]);
	}
	return finish(raw.closed);
}
function attrNum(attrs, key, fallback = 0) {
	const v = attrs[key];
	if (v === void 0) return fallback;
	const x = typeof v === "number" ? v : Number(v);
	return Number.isFinite(x) ? x : fallback;
}
function parsePoints(v) {
	const s = String(v ?? "").trim();
	if (!s) return [];
	const nums = s.split(/[\s,]+/).map(Number);
	if (nums.some((x) => !Number.isFinite(x))) throw new Error(`morphicons: invalid points: "${s}"`);
	return nums;
}
function polyPath(nums, closed) {
	if (nums.length < 4) return null;
	const [, line, , , finish] = builder(nums[0], nums[1]);
	for (let i = 2; i + 1 < nums.length; i += 2) line(nums[i], nums[i + 1]);
	return finish(closed);
}
function ellipsePath(cx, cy, rx, ry) {
	if (rx < 1e-12 || ry < 1e-12) return null;
	const kx = KAPPA * rx;
	const ky = KAPPA * ry;
	const e = cx + rx;
	const w = cx - rx;
	const s = cy + ry;
	const n = cy - ry;
	const [cubic, , , , finish] = builder(e, cy);
	cubic(e, cy + ky, cx + kx, s, cx, s);
	cubic(cx - kx, s, w, cy + ky, w, cy);
	cubic(w, cy - ky, cx - kx, n, cx, n);
	cubic(cx + kx, n, e, cy - ky, e, cy);
	return finish(true);
}
function rectPath(attrs) {
	const x = attrNum(attrs, "x");
	const y = attrNum(attrs, "y");
	const w = attrNum(attrs, "width");
	const h = attrNum(attrs, "height");
	if (w < 1e-12 || h < 1e-12) return null;
	let rx = attrNum(attrs, "rx", NaN);
	let ry = attrNum(attrs, "ry", NaN);
	if (Number.isNaN(rx)) rx = Number.isNaN(ry) ? 0 : ry;
	if (Number.isNaN(ry)) ry = rx;
	rx = Math.min(Math.max(rx, 0), w / 2);
	ry = Math.min(Math.max(ry, 0), h / 2);
	if (rx < 1e-12 || ry < 1e-12) return polyPath([
		x,
		y,
		x + w,
		y,
		x + w,
		y + h,
		x,
		y + h
	], true);
	const xa = x + rx;
	const xb = x + w - rx;
	const xr = x + w;
	const ya = y + ry;
	const yb = y + h - ry;
	const yd = y + h;
	const kx = KAPPA * rx;
	const ky = KAPPA * ry;
	const [cubic, line, , , finish] = builder(xa, y);
	line(xb, y);
	cubic(xb + kx, y, xr, ya - ky, xr, ya);
	line(xr, yb);
	cubic(xr, yb + ky, xb + kx, yd, xb, yd);
	line(xa, yd);
	cubic(xa - kx, yd, x, yb + ky, x, yb);
	line(x, ya);
	cubic(x, ya - ky, xa - kx, y, xa, y);
	return finish(true);
}
/** Icon (IconNode or `d` string) → list of cubic subpaths. */
function iconToCubics(input) {
	const out = [];
	const push = (p) => {
		if (p) out.push(p);
	};
	if (typeof input === "string") {
		for (const s of parsePath(input)) push(lowerSubpath(s));
		return out;
	}
	for (const [tag, attrs] of input) switch (tag) {
		case "path":
			for (const s of parsePath(String(attrs.d ?? ""))) push(lowerSubpath(s));
			break;
		case "line": {
			const [, line, , , finish] = builder(attrNum(attrs, "x1"), attrNum(attrs, "y1"));
			line(attrNum(attrs, "x2"), attrNum(attrs, "y2"));
			push(finish(false));
			break;
		}
		case "circle": {
			const r = attrNum(attrs, "r");
			push(ellipsePath(attrNum(attrs, "cx"), attrNum(attrs, "cy"), r, r));
			break;
		}
		case "ellipse":
			push(ellipsePath(attrNum(attrs, "cx"), attrNum(attrs, "cy"), attrNum(attrs, "rx"), attrNum(attrs, "ry")));
			break;
		case "rect":
			push(rectPath(attrs));
			break;
		case "polyline":
			push(polyPath(parsePoints(attrs.points), false));
			break;
		case "polygon":
			push(polyPath(parsePoints(attrs.points), true));
			break;
		default: throw new Error(`morphicons: unsupported tag <${tag}>`);
	}
	return out;
}
function parseViewBox(vb) {
	const v = typeof vb === "number" ? [
		0,
		0,
		vb,
		vb
	] : typeof vb === "string" ? vb.trim().split(/[\s,]+/).map(Number) : vb;
	const [minX, minY, w, h] = v;
	if (v.length !== 4 || !(w > 0) || !(h > 0) || !Number.isFinite(minX) || !Number.isFinite(minY)) throw new Error(`morphicons: invalid viewBox "${String(vb)}"`);
	return [
		minX,
		minY,
		w,
		h
	];
}
/** Re-grids an icon drawn on `viewBox` onto the shared `grid` (24 by default),
*  centred and preserving aspect ratio — the SVG `xMidYMid meet` rule.
*
*  Both endpoints of a morph must live on the same coordinate space. Lucide and
*  Tabler already draw on 24×24; packs on 20 (Heroicons solid) or 32 (Carbon)
*  do not, and mixing them unfitted makes Procrustes read the scale/offset gap
*  as rotation. Apply once at module scope (not per render) and pass the
*  resulting `d` anywhere an icon is accepted. */
function fitIcon(input, viewBox, grid = 24) {
	const [minX, minY, w, h] = parseViewBox(viewBox);
	const s = Math.min(grid / w, grid / h);
	const tx = (grid - w * s) / 2 - minX * s;
	const ty = (grid - h * s) / 2 - minY * s;
	const paths = iconToCubics(input);
	for (const { pts } of paths) for (let i = 0; i < pts.length; i += 2) {
		pts[i] = pts[i] * s + tx;
		pts[i + 1] = pts[i + 1] * s + ty;
	}
	return cubicsToPathD(paths);
}
//#endregion
export { serialize as i, iconToCubics as n, cubicsToPathD as r, fitIcon as t };
