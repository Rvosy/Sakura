import { n as iconToCubics } from "./normalize-CYnN3Npw.js";
//#region src/core/interpolate.ts
/** Preallocated output buffers for a plan (zero allocation per frame). */
function allocOutputs(plan) {
	return plan.items.map(() => new Float64Array(2 * plan.n));
}
function interpPolar(plan, t, out) {
	for (let k = 0; k < plan.items.length; k++) {
		const it = plan.items[k];
		const o = out[k];
		const n = plan.n;
		const s = Math.exp(it.lnSigma * t);
		const ang = it.theta * t;
		const cos = Math.cos(ang) * s;
		const sin = Math.sin(ang) * s;
		let cx;
		let cy;
		if (it.block) {
			const [ox, oy] = it.block.off;
			const [dx, dy] = it.block.drift;
			cx = it.ca[0] + dx * t + (ox * cos - oy * sin - ox);
			cy = it.ca[1] + dy * t + (ox * sin + oy * cos - oy);
		} else {
			cx = it.ca[0] + (it.cb[0] - it.ca[0]) * t;
			cy = it.ca[1] + (it.cb[1] - it.ca[1]) * t;
		}
		for (let i = 0; i < n; i++) {
			const px = it.aC[2 * i] + (it.bT[2 * i] - it.aC[2 * i]) * t;
			const py = it.aC[2 * i + 1] + (it.bT[2 * i + 1] - it.aC[2 * i + 1]) * t;
			o[2 * i] = cx + px * cos - py * sin;
			o[2 * i + 1] = cy + px * sin + py * cos;
		}
	}
}
/** Raw coordinate lerp (same correspondence, no decomposition). */
function interpLinear(plan, t, out) {
	for (let k = 0; k < plan.items.length; k++) {
		const it = plan.items[k];
		const o = out[k];
		const n = plan.n;
		for (let i = 0; i < n; i++) {
			o[2 * i] = it.a[2 * i] + (it.bO[2 * i] - it.a[2 * i]) * t;
			o[2 * i + 1] = it.a[2 * i + 1] + (it.bO[2 * i + 1] - it.a[2 * i + 1]) * t;
		}
	}
}
//#endregion
//#region src/core/plan.ts
/** Weight of |ΔL| in the subpath pairing cost. */
const LEN_WEIGHT = .35;
/** λ of the minimal-rotation tie-break: score = res + λ·|θ|/π.
*  It exists because shapes symmetric under inversion (lines) tie in
*  residual for both traversal orientations yet produce different rotations. */
const LAMBDA = .05;
/** Global residual below which the whole icon counts as congruent and the
*  plan shares (θ, σ) across all items (hybrid variant of Procrustes). */
const GLOBAL_EPS = .005;
/** Bounds for exhaustive matching; above them it falls back to greedy with
*  repair. 8! = 40 320 permutations / 1e5 assignments — both sub-ms. */
const PERM_MAX = 8;
const SURJ_MAX = 1e5;
function centroid(p) {
	const n = p.length / 2;
	let cx = 0;
	let cy = 0;
	for (let i = 0; i < n; i++) {
		cx += p[2 * i];
		cy += p[2 * i + 1];
	}
	return [cx / n, cy / n];
}
function polyLen(p) {
	const n = p.length / 2;
	let L = 0;
	for (let i = 1; i < n; i++) L += Math.hypot(p[2 * i] - p[2 * i - 2], p[2 * i + 1] - p[2 * i - 1]);
	return L;
}
function reversePts(p) {
	const n = p.length / 2;
	const out = new Float64Array(2 * n);
	for (let i = 0; i < n; i++) {
		out[2 * i] = p[2 * (n - 1 - i)];
		out[2 * i + 1] = p[2 * (n - 1 - i) + 1];
	}
	return out;
}
/** Circular re-indexing of a loop: out[i] = p[(i+off) mod n]. Same point
*  set, different cut point — the circular degree of freedom of closed paths. */
function rotatePts(p, off) {
	const n = p.length / 2;
	const out = new Float64Array(2 * n);
	for (let i = 0; i < n; i++) {
		const j = (i + off) % n;
		out[2 * i] = p[2 * j];
		out[2 * i + 1] = p[2 * j + 1];
	}
	return out;
}
/** Optimal similarity (θ, σ) minimizing Σ|σ·R(θ)·(a−c_A) − (b−c_B)|².
*  θ* = atan2(S_xy − S_yx, S_xx + S_yy); σ* by zero derivative.
*  res = RMS residual normalized by b's energy (0 → same shape). */
function procrustes(a, b, ca, cb) {
	const n = a.length / 2;
	let sxx = 0;
	let sxy = 0;
	let syx = 0;
	let syy = 0;
	let na = 0;
	let nb = 0;
	for (let i = 0; i < n; i++) {
		const ax = a[2 * i] - ca[0];
		const ay = a[2 * i + 1] - ca[1];
		const bx = b[2 * i] - cb[0];
		const by = b[2 * i + 1] - cb[1];
		sxx += ax * bx;
		syy += ay * by;
		sxy += ax * by;
		syx += ay * bx;
		na += ax * ax + ay * ay;
		nb += bx * bx + by * by;
	}
	const theta = Math.atan2(sxy - syx, sxx + syy);
	const num = Math.cos(theta) * (sxx + syy) + Math.sin(theta) * (sxy - syx);
	let sigma = na > 1e-12 ? num / na : 1;
	if (!(sigma > 1e-6)) sigma = 1e-6;
	const res2 = Math.max(0, sigma * sigma * na - 2 * sigma * num + nb);
	const res = nb > 1e-12 ? Math.sqrt(res2 / nb) : 0;
	return {
		theta,
		sigma,
		res
	};
}
/** Best index-to-index correspondence between a and b: tries both traversal
*  directions and, if there is a closed loop, its N circular offsets,
*  scoring with score = res + λ·|θ|/π. The freedom is applied to ONE cloud
*  — the closed one (b if both are); varying both at once would be
*  redundant. */
function alignPair(aPts, bPts, aClosed = false, bClosed = false) {
	const ca = centroid(aPts);
	const cb = centroid(bPts);
	const varyA = aClosed && !bClosed;
	const base = varyA ? aPts : bPts;
	const offs = aClosed || bClosed ? base.length / 2 : 1;
	let bestScore = Number.POSITIVE_INFINITY;
	let best = base;
	let sim = {
		theta: 0,
		sigma: 1,
		res: 0
	};
	for (let dir = 0; dir < 2; dir++) {
		const walk = dir ? reversePts(base) : base;
		for (let off = 0; off < offs; off++) {
			const cand = off ? rotatePts(walk, off) : walk;
			const s = varyA ? procrustes(cand, bPts, ca, cb) : procrustes(aPts, cand, ca, cb);
			const score = s.res + LAMBDA * Math.abs(s.theta) / Math.PI;
			if (score < bestScore) {
				bestScore = score;
				best = cand;
				sim = s;
			}
		}
	}
	return varyA ? {
		ca,
		cb,
		a: best,
		b: bPts,
		...sim
	} : {
		ca,
		cb,
		a: aPts,
		b: best,
		...sim
	};
}
function costMatrix(A, B) {
	const cbs = B.map(centroid);
	const lbs = B.map(polyLen);
	return A.map((a) => {
		const ca = centroid(a);
		const la = polyLen(a);
		return cbs.map((cb, j) => Math.hypot(ca[0] - cb[0], ca[1] - cb[1]) + LEN_WEIGHT * Math.abs(la - lbs[j]));
	});
}
function bestPermutation(C) {
	const n = C.length;
	if (n > PERM_MAX) {
		const pairs = [];
		for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) pairs.push([
			C[i][j],
			i,
			j
		]);
		pairs.sort((x, y) => x[0] - y[0]);
		const out = new Array(n).fill(-1);
		const used = new Array(n).fill(false);
		for (const [, i, j] of pairs) if (out[i] < 0 && !used[j]) {
			out[i] = j;
			used[j] = true;
		}
		return out;
	}
	const idx = Array.from({ length: n }, (_, i) => i);
	let best = idx.slice();
	let bc = Number.POSITIVE_INFINITY;
	const perm = (arr, k, acc) => {
		if (acc >= bc) return;
		if (k === n) {
			bc = acc;
			best = arr.slice();
			return;
		}
		for (let i = k; i < n; i++) {
			[arr[k], arr[i]] = [arr[i], arr[k]];
			perm(arr, k + 1, acc + C[k][arr[k]]);
			[arr[k], arr[i]] = [arr[i], arr[k]];
		}
	};
	perm(idx, 0, 0);
	return best;
}
function bestSurjection(C) {
	const B = C.length;
	const S = C[0].length;
	if (S ** B > SURJ_MAX) {
		const f = C.map((row) => {
			let m = 0;
			for (let j = 1; j < row.length; j++) if (row[j] < row[m]) m = j;
			return m;
		});
		const mult = new Array(S).fill(0);
		for (const s of f) mult[s]++;
		for (let s = 0; s < S; s++) {
			if (mult[s] > 0) continue;
			let bi = -1;
			let bc = Number.POSITIVE_INFINITY;
			for (let i = 0; i < B; i++) {
				if (mult[f[i]] < 2) continue;
				const extra = C[i][s] - C[i][f[i]];
				if (extra < bc) {
					bc = extra;
					bi = i;
				}
			}
			mult[f[bi]]--;
			f[bi] = s;
			mult[s]++;
		}
		return f;
	}
	let best = null;
	let bc = Number.POSITIVE_INFINITY;
	const f = new Array(B);
	const mult = new Array(S).fill(0);
	const rec = (i, acc, covered) => {
		if (acc >= bc || S - covered > B - i) return;
		if (i === B) {
			bc = acc;
			best = f.slice();
			return;
		}
		for (let s = 0; s < S; s++) {
			f[i] = s;
			mult[s]++;
			rec(i + 1, acc + C[i][s], covered + (mult[s] === 1 ? 1 : 0));
			mult[s]--;
		}
	};
	rec(0, 0, 0);
	if (!best) throw new Error("morphicons: no valid surjection (B < S)");
	return best;
}
function applyGlobal(items, n) {
	const T = items.length * n;
	const ga = new Float64Array(2 * T);
	const gb = new Float64Array(2 * T);
	items.forEach((it, k) => {
		ga.set(it.a, 2 * n * k);
		gb.set(it.bO, 2 * n * k);
	});
	const gca = centroid(ga);
	const g = procrustes(ga, gb, gca, centroid(gb));
	if (g.res >= GLOBAL_EPS) return;
	const cos = Math.cos(-g.theta);
	const sin = Math.sin(-g.theta);
	const rc = Math.cos(g.theta);
	const rs = Math.sin(g.theta);
	for (const it of items) {
		let e2 = 0;
		let nb = 0;
		for (let i = 0; i < n; i++) {
			const bx = it.bO[2 * i] - it.cb[0];
			const by = it.bO[2 * i + 1] - it.cb[1];
			it.bT[2 * i] = (bx * cos - by * sin) / g.sigma;
			it.bT[2 * i + 1] = (bx * sin + by * cos) / g.sigma;
			const ex = g.sigma * (rc * it.aC[2 * i] - rs * it.aC[2 * i + 1]) - bx;
			const ey = g.sigma * (rs * it.aC[2 * i] + rc * it.aC[2 * i + 1]) - by;
			e2 += ex * ex + ey * ey;
			nb += bx * bx + by * by;
		}
		it.theta = g.theta;
		it.lnSigma = Math.log(g.sigma);
		it.res = nb > 1e-12 ? Math.sqrt(e2 / nb) : 0;
		const s1 = Math.exp(it.lnSigma);
		const c1 = Math.cos(it.theta) * s1;
		const n1 = Math.sin(it.theta) * s1;
		const ox = it.ca[0] - gca[0];
		const oy = it.ca[1] - gca[1];
		const rx = ox * c1 - oy * n1 - ox;
		const ry = ox * n1 + oy * c1 - oy;
		it.block = {
			off: [ox, oy],
			drift: [it.cb[0] - it.ca[0] - rx, it.cb[1] - it.ca[1] - ry]
		};
	}
}
/** Builds the morph plan between two lists of sampled subpaths. The plan is
*  cacheable and serializable; it accepts any list — including intermediate
*  shapes (interruptions). */
function buildPlan(srcSubs, dstSubs) {
	const p = srcSubs.length;
	const q = dstSubs.length;
	if (p === 0 || q === 0) throw new Error("morphicons: icon has no subpaths");
	const A = srcSubs.map((s) => s.pts);
	const B = dstSubs.map((s) => s.pts);
	const pairs = [];
	if (p === q) {
		const perm = bestPermutation(costMatrix(A, B));
		for (let i = 0; i < p; i++) pairs.push([i, perm[i]]);
	} else if (p < q) {
		const f = bestSurjection(costMatrix(B, A));
		for (let j = 0; j < q; j++) pairs.push([f[j], j]);
	} else {
		const f = bestSurjection(costMatrix(A, B));
		for (let i = 0; i < p; i++) pairs.push([i, f[i]]);
	}
	const n = A[0].length / 2;
	const items = pairs.map(([si, di]) => {
		const al = alignPair(A[si], B[di], srcSubs[si].closed, dstSubs[di].closed);
		const a = al.a;
		const aC = new Float64Array(2 * n);
		const bT = new Float64Array(2 * n);
		const bO = new Float64Array(2 * n);
		const cos = Math.cos(-al.theta);
		const sin = Math.sin(-al.theta);
		for (let i = 0; i < n; i++) {
			aC[2 * i] = a[2 * i] - al.ca[0];
			aC[2 * i + 1] = a[2 * i + 1] - al.ca[1];
			const bx = al.b[2 * i] - al.cb[0];
			const by = al.b[2 * i + 1] - al.cb[1];
			bT[2 * i] = (bx * cos - by * sin) / al.sigma;
			bT[2 * i + 1] = (bx * sin + by * cos) / al.sigma;
			bO[2 * i] = al.b[2 * i];
			bO[2 * i + 1] = al.b[2 * i + 1];
		}
		return {
			a,
			aC,
			bT,
			bO,
			ca: al.ca,
			cb: al.cb,
			theta: al.theta,
			lnSigma: Math.log(al.sigma),
			res: al.res,
			closed: srcSubs[si].closed && dstSubs[di].closed,
			block: null
		};
	});
	if (items.length > 1) applyGlobal(items, n);
	return {
		items,
		n
	};
}
//#endregion
//#region src/core/resample.ts
/** Default angular threshold for a segment joint to count as a corner. */
const CORNER_THRESHOLD = Math.PI / 8;
const GX = [
	.18343464249564978,
	.525532409916329,
	.7966664774136267,
	.9602898564975363
];
const GW = [
	.362683783378362,
	.31370664587788727,
	.22238103445337448,
	.10122853629037626
];
function speed(p, k, t) {
	const i = 6 * k;
	const u = 1 - t;
	const c0 = 3 * u * u;
	const c1 = 6 * u * t;
	const c2 = 3 * t * t;
	const dx = c0 * (p[i + 2] - p[i]) + c1 * (p[i + 4] - p[i + 2]) + c2 * (p[i + 6] - p[i + 4]);
	const dy = c0 * (p[i + 3] - p[i + 1]) + c1 * (p[i + 5] - p[i + 3]) + c2 * (p[i + 7] - p[i + 5]);
	return Math.hypot(dx, dy);
}
function segLen(p, k, t1 = 1) {
	const half = t1 / 2;
	let s = 0;
	for (let j = 0; j < 4; j++) s += GW[j] * (speed(p, k, half + half * GX[j]) + speed(p, k, half - half * GX[j]));
	return s * half;
}
function point(p, k, t, out, o) {
	const i = 6 * k;
	const u = 1 - t;
	const b0 = u * u * u;
	const b1 = 3 * u * u * t;
	const b2 = 3 * u * t * t;
	const b3 = t * t * t;
	out[o] = b0 * p[i] + b1 * p[i + 2] + b2 * p[i + 4] + b3 * p[i + 6];
	out[o + 1] = b0 * p[i + 1] + b1 * p[i + 3] + b2 * p[i + 5] + b3 * p[i + 7];
}
function tangent(p, k, atEnd) {
	const i = 6 * k;
	const b = atEnd ? i + 6 : i;
	const s = atEnd ? -1 : 1;
	for (const j of atEnd ? [
		4,
		2,
		0
	] : [
		2,
		4,
		6
	]) {
		const dx = s * (p[i + j] - p[b]);
		const dy = s * (p[i + j + 1] - p[b + 1]);
		if (dx * dx + dy * dy > 1e-18) return [dx, dy];
	}
	return null;
}
/** Segment boundaries (index of the segment starting at the corner) whose
*  tangent discontinuity exceeds the threshold. For closed paths this
*  includes the closing joint (boundary = first active segment). */
function detectCorners(path, threshold = CORNER_THRESHOLD) {
	const p = path.pts;
	const m = (p.length / 2 - 1) / 3;
	const active = [];
	for (let k = 0; k < m; k++) if (segLen(p, k) > 1e-9) active.push(k);
	if (active.length === 0) return [];
	const corners = /* @__PURE__ */ new Set();
	const test = (a, b) => {
		const u = tangent(p, a, true);
		const v = tangent(p, b, false);
		if (!u || !v) return;
		if (Math.abs(Math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])) > threshold) corners.add(b);
	};
	for (let j = 0; j + 1 < active.length; j++) test(active[j], active[j + 1]);
	if (path.closed && active.length > 1) test(active[active.length - 1], active[0]);
	return [...corners].sort((a, b) => a - b);
}
function invert(p, k, s, ls) {
	if (s <= 0) return 0;
	if (s >= ls) return 1;
	let lo = 0;
	let hi = 1;
	let t = s / ls;
	for (let it = 0; it < 12; it++) {
		const f = segLen(p, k, t) - s;
		if (Math.abs(f) < 1e-10 * ls + 1e-14) break;
		if (f > 0) hi = t;
		else lo = t;
		const sp = speed(p, k, t);
		let nt = sp > 1e-12 ? t - f / sp : (lo + hi) / 2;
		if (!(nt > lo && nt < hi)) nt = (lo + hi) / 2;
		t = nt;
	}
	return t;
}
/** Samples a cubic subpath at N points equidistant by arc length, anchoring
*  corners and endpoints as exact samples. Returns Float64Array(2N). Closed
*  paths distribute N intervals around the loop (without duplicating the
*  first point); the circular start-point freedom is resolved by the plan's
*  circular correspondence. */
function resamplePath(path, N = 64, cornerThreshold = CORNER_THRESHOLD) {
	const p = path.pts;
	const m = (p.length / 2 - 1) / 3;
	const out = new Float64Array(2 * N);
	const fill = () => {
		for (let i = 0; i < N; i++) {
			out[2 * i] = p[0];
			out[2 * i + 1] = p[1];
		}
		return out;
	};
	if (m < 1) return fill();
	const lens = new Array(m);
	let L = 0;
	for (let k = 0; k < m; k++) {
		lens[k] = segLen(p, k);
		L += lens[k];
	}
	if (L < 1e-12) return fill();
	const cs = detectCorners(path, cornerThreshold);
	const anchors = path.closed ? cs.length > 0 ? cs : [0] : [.../* @__PURE__ */ new Set([
		0,
		...cs,
		m
	])].sort((a, b) => a - b);
	const runs = [];
	if (path.closed) for (let j = 0; j < anchors.length; j++) {
		const a = anchors[j];
		const b = j + 1 < anchors.length ? anchors[j + 1] : anchors[0] + m;
		runs.push([a, b]);
	}
	else for (let j = 0; j + 1 < anchors.length; j++) runs.push([anchors[j], anchors[j + 1]]);
	const rl = runs.map(([a, b]) => {
		let s = 0;
		for (let k = a; k < b; k++) s += lens[k % m];
		return s;
	});
	const intervals = path.closed ? N : N - 1;
	if (runs.length > intervals) throw new Error(`morphicons: N=${N} too small (${runs.length} runs)`);
	const total = rl.reduce((a, b) => a + b, 0) || 1;
	const ideal = rl.map((l) => intervals * l / total);
	const counts = ideal.map((q) => Math.max(1, Math.floor(q)));
	let R = intervals - counts.reduce((a, b) => a + b, 0);
	if (R > 0) {
		const order = ideal.map((q, idx) => [Math.round((q - Math.floor(q)) * 1e9), idx]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
		for (let j = 0; j < R; j++) counts[order[j % counts.length][1]]++;
	}
	while (R < 0) {
		let bi = 0;
		for (let idx = 1; idx < counts.length; idx++) if (counts[idx] > counts[bi]) bi = idx;
		if (counts[bi] <= 1) break;
		counts[bi]--;
		R++;
	}
	let w = 0;
	for (let r = 0; r < runs.length; r++) {
		const [k0, k1] = runs[r];
		const cnt = counts[r];
		const Lr = rl[r];
		const vi = 6 * (k0 % m);
		out[2 * w] = p[vi];
		out[2 * w + 1] = p[vi + 1];
		w++;
		let seg = k0;
		let acc = 0;
		for (let j = 1; j < cnt; j++) {
			const target = Lr * j / cnt;
			while (seg < k1 - 1 && acc + lens[seg % m] < target) {
				acc += lens[seg % m];
				seg++;
			}
			const k = seg % m;
			const ls = lens[k];
			point(p, k, ls > 1e-12 ? invert(p, k, target - acc, ls) : 0, out, 2 * w);
			w++;
		}
	}
	if (!path.closed) {
		const vi = 6 * m;
		out[2 * w] = p[vi];
		out[2 * w + 1] = p[vi + 1];
	}
	return out;
}
/** Full input pipeline: icon → cubics → sampled subpaths with their
*  topology (the plan needs to know which subpaths are closed loops). */
function resampleIcon(input, N = 64) {
	return iconToCubics(input).map((path) => ({
		pts: resamplePath(path, N),
		closed: path.closed
	}));
}
//#endregion
//#region src/core/spring.ts
var Spring = class {
	x = 1;
	v = 0;
	k = 250;
	c = 24;
	config(k, c) {
		this.k = k;
		this.c = c;
	}
	/** Starts (or restarts mid-flight) preserving velocity. */
	start() {
		this.x = 0;
		if (this.v > 14) this.v = 14;
		if (this.v < -14) this.v = -14;
	}
	/** Advances dt seconds. Returns true on settle (|1−x| < 0.001 ∧ |v| < 0.02). */
	step(dt) {
		const steps = Math.max(1, Math.min(16, Math.ceil(dt / (1 / 240))));
		const s = dt / steps;
		for (let i = 0; i < steps; i++) {
			const a = this.k * (1 - this.x) - this.c * this.v;
			this.v += a * s;
			this.x += this.v * s;
		}
		return Math.abs(1 - this.x) < .001 && Math.abs(this.v) < .02;
	}
};
/** Spring presets (ζ = c/(2√k)) with the API's public names. */
const SPRING_PRESETS = {
	/** ζ = 1.00 — critically damped, no overshoot. */
	smooth: {
		k: 170,
		c: 26
	},
	/** ζ = 0.73 — fast, subtle overshoot. */
	snappy: {
		k: 420,
		c: 30
	},
	/** ζ = 0.40 — playful. */
	bouncy: {
		k: 300,
		c: 14
	}
};
//#endregion
export { buildPlan as a, interpPolar as c, resamplePath as i, Spring as n, allocOutputs as o, resampleIcon as r, interpLinear as s, SPRING_PRESETS as t };
