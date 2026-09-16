import { a as buildPlan, c as interpPolar, n as Spring, o as allocOutputs, r as resampleIcon, t as SPRING_PRESETS } from "./spring-CFHloqPP.js";
import { i as serialize, n as iconToCubics, r as cubicsToPathD } from "./normalize-CYnN3Npw.js";
//#region src/dom/index.ts
const tickers = /* @__PURE__ */ new Set();
let rafId = 0;
let last = -1;
function loop(ts) {
	const dt = last < 0 ? 0 : Math.min(Math.max((ts - last) / 1e3, 0), .1);
	last = ts;
	for (const tick of [...tickers]) tick(dt);
	if (tickers.size > 0) rafId = requestAnimationFrame(loop);
	else {
		rafId = 0;
		last = -1;
	}
}
function addTicker(tick) {
	tickers.add(tick);
	if (rafId === 0) {
		last = -1;
		rafId = requestAnimationFrame(loop);
	}
}
function removeTicker(tick) {
	tickers.delete(tick);
	if (tickers.size === 0 && rafId !== 0) {
		cancelAnimationFrame(rafId);
		rafId = 0;
		last = -1;
	}
}
const samples = /* @__PURE__ */ new WeakMap();
const canon = /* @__PURE__ */ new WeakMap();
const plans = /* @__PURE__ */ new WeakMap();
function sampledOf(icon) {
	if (typeof icon === "string") return resampleIcon(icon);
	let s = samples.get(icon);
	if (!s) {
		s = resampleIcon(icon);
		samples.set(icon, s);
	}
	return s;
}
/** Canonical `d` of an icon: the input string verbatim, or the real cubics
*  quantized to 4 decimals (the at-rest snap; engine-stable bytes so SSR
*  hydration matches, see fmtCanon in core/serialize). Exported because it
*  is what a binding renders at SSR/rest before any runtime exists. */
function canonicalD(icon) {
	if (typeof icon === "string") return icon;
	let d = canon.get(icon);
	if (!d) {
		d = cubicsToPathD(iconToCubics(icon));
		canon.set(icon, d);
	}
	return d;
}
function planBetween(src, dst) {
	if (typeof src === "string" || typeof dst === "string") return buildPlan(sampledOf(src), sampledOf(dst));
	let inner = plans.get(src);
	if (!inner) {
		inner = /* @__PURE__ */ new WeakMap();
		plans.set(src, inner);
	}
	let p = inner.get(dst);
	if (!p) {
		p = buildPlan(sampledOf(src), sampledOf(dst));
		inner.set(dst, p);
	}
	return p;
}
function resolveSpring(s) {
	if (typeof s === "string") return SPRING_PRESETS[s];
	const d = SPRING_PRESETS.snappy;
	return {
		k: s?.stiffness ?? d.k,
		c: s?.damping ?? d.c
	};
}
/** Creates the morph instance over a `<path>` and paints the initial icon. */
function createMorph(el, icon, options) {
	const spring = new Spring();
	let reducedMotion = options?.reducedMotion ?? "never";
	let target = icon;
	let rest = true;
	let plan = null;
	let out = null;
	let closed = null;
	let t = 1;
	let flying = false;
	let dead = false;
	el.setAttribute("d", canonicalD(icon));
	const render = (tt) => {
		const p = plan;
		const o = out;
		const cl = closed;
		if (!p || !o || !cl) return;
		t = tt;
		interpPolar(p, tt, o);
		el.setAttribute("d", serialize(o, cl));
	};
	const stop = () => {
		if (!flying) return;
		flying = false;
		removeTicker(tick);
	};
	const tick = (dt) => {
		const settled = spring.step(dt);
		render(spring.x);
		if (settled) {
			stop();
			settle();
		}
	};
	const settle = () => {
		rest = true;
		plan = null;
		out = null;
		closed = null;
		t = 1;
		spring.x = 1;
		spring.v = 0;
		el.setAttribute("d", canonicalD(target));
	};
	/** The current shape as plan source: the at-rest icon, or the rendered
	*  buffers (already N points per subpath). */
	const snapshot = () => {
		const p = plan;
		const o = out;
		if (rest || !p || !o) return sampledOf(target);
		return o.map((buf, k) => ({
			pts: Float64Array.from(buf),
			closed: p.items[k].closed
		}));
	};
	const retarget = (icon) => {
		plan = rest ? planBetween(target, icon) : buildPlan(snapshot(), sampledOf(icon));
		out = allocOutputs(plan);
		closed = plan.items.map((it) => it.closed);
		target = icon;
		rest = false;
	};
	const setNow = (icon) => {
		stop();
		target = icon;
		settle();
	};
	/** True when the policy says this morphTo must jump instead of flying. */
	const motionOff = () => {
		if (reducedMotion === "always") return true;
		if (reducedMotion !== "user") return false;
		if (typeof matchMedia === "undefined") return false;
		return matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
	};
	const seek = (icon, tt) => {
		if (dead) return;
		const reuse = !rest && plan !== null && icon === target;
		stop();
		spring.v = 0;
		if (!reuse) retarget(icon);
		render(tt);
	};
	return {
		morphTo(icon, sp) {
			if (dead) return;
			if (icon === target && (rest || flying)) return;
			if (motionOff()) {
				setNow(icon);
				return;
			}
			const { k, c } = resolveSpring(sp);
			spring.config(k, c);
			retarget(icon);
			spring.start();
			if (!flying) {
				flying = true;
				addTicker(tick);
			}
		},
		set(icon) {
			if (dead) return;
			setNow(icon);
		},
		seek,
		get progress() {
			return rest ? 1 : t;
		},
		set progress(v) {
			if (!dead) seek(target, v);
		},
		get reducedMotion() {
			return reducedMotion;
		},
		set reducedMotion(v) {
			reducedMotion = v;
		},
		destroy() {
			stop();
			dead = true;
			plan = null;
			out = null;
			closed = null;
		}
	};
}
//#endregion
export { canonicalD, createMorph };
