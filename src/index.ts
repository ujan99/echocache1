/**
 * EchoCache - predictive serverless cache pre-warmer
 *
 * Two arms share this one Worker so that both are served by identical code:
 *   ?arm=off  reactive caching only  (control)
 *   ?arm=on   reactive + predictive pre-warming (treatment)
 * Cache keys are namespaced by arm, so the arms never share cache state.
 *
 * Every response carries X-Cache: MISS | REACTIVE | PREWARM so the benchmark
 * can tell WHY a request was fast, which is the measurement the project needs.
 */

export interface Env {
	echocache_logs: D1Database;
	CACHE: KVNamespace;
}

/** KV refuses any expirationTtl below 60 seconds, so 60 is the floor we must
 *  pass to put(). It is NOT the TTL the experiment uses. */
const KV_FLOOR_SECONDS = 60;

/** The TTL the experiment actually varies. Enforced in code: an entry older
 *  than this is treated as a miss even though KV still holds it. Both arms
 *  use this same value - unequal TTLs would flatter the treatment arm. */
const SOFT_TTL_MS = 20_000;

/** How far back the predictor looks when counting popularity / learning pairs. */
const WINDOW_MS = 15 * 60 * 1000;

/** How many popular items the scheduled predictor refreshes per run. */
const TOP_N = 5;

type Arm = 'on' | 'off';
type Source = 'MISS' | 'REACTIVE' | 'PREWARM';

interface Entry {
	data: string;
	source: 'REACTIVE' | 'PREWARM';
	at: number;
}

/** An entry counts as cached only while it is inside the soft TTL. */
const fresh = (e: Entry | null): e is Entry => e !== null && Date.now() - e.at < SOFT_TTL_MS;

/** Read an entry, tolerating anything in KV that is not our JSON shape
 *  (a legacy plain-string value would otherwise throw and 500 the request). */
async function cacheGet(env: Env, arm: Arm, itemId: string): Promise<Entry | null> {
	try {
		return (await env.CACHE.get(itemKey(arm, itemId), 'json')) as Entry | null;
	} catch {
		return null;
	}
}

const itemKey = (arm: Arm, item: string) => `${arm}:item:${item}`;
const modelKey = (arm: Arm) => `${arm}:model:successors`;

/** The "slow backend". Reading from D1 is a genuine cross-region round trip,
 *  so the miss penalty is real rather than an artificial sleep. */
async function readOrigin(env: Env, itemId: string): Promise<string> {
	const row = await env.echocache_logs
		.prepare('SELECT payload FROM items WHERE item_id = ?')
		.bind(itemId)
		.first<{ payload: string }>();
	return row?.payload ?? `origin-miss:${itemId}`;
}

/** Write an entry, tagging how it got there. Never overwrites an existing
 *  entry: that would destroy the REACTIVE/PREWARM attribution. */
async function cachePut(env: Env, arm: Arm, itemId: string, data: string, source: 'REACTIVE' | 'PREWARM'): Promise<boolean> {
	const key = itemKey(arm, itemId);
	if (source === 'PREWARM') {
		const existing = await cacheGet(env, arm, itemId);
		if (fresh(existing)) return false; // already warm, nothing gained
	}
	const entry: Entry = { data, source, at: Date.now() };
	await env.CACHE.put(key, JSON.stringify(entry), { expirationTtl: KV_FLOOR_SECONDS });
	return true;
}

/** Record every pre-warm write so precision can be computed afterwards. */
async function logPrewarm(env: Env, arm: Arm, itemId: string, reason: string): Promise<void> {
	await env.echocache_logs
		.prepare('INSERT INTO prewarm_log (item_id, ts, arm, reason) VALUES (?, ?, ?, ?)')
		.bind(itemId, Date.now(), arm, reason)
		.run();
}

/** Sequence prediction: having just served X, warm the item that usually
 *  follows X in a session. This is the part plain LRU cannot do. */
async function prewarmSuccessor(env: Env, arm: Arm, itemId: string): Promise<void> {
	const model = (await env.CACHE.get(modelKey(arm), 'json')) as Record<string, string> | null;
	if (!model) return;
	const next = model[itemId];
	if (!next) return;

	const payload = await readOrigin(env, next);
	const written = await cachePut(env, arm, next, payload, 'PREWARM');
	if (written) await logPrewarm(env, arm, next, 'successor');
}

/** Popularity refresh + rebuild of the successor model. Called by cron and
 *  by /admin/predict (so a benchmark can trigger it without waiting 5 min). */
async function runPredictor(env: Env, arm: Arm): Promise<{ warmed: string[]; pairs: number }> {
	const since = Date.now() - WINDOW_MS;
	const warmed: string[] = [];

	// 1. popularity over the recent window only, not all time
	const pop = await env.echocache_logs
		.prepare(
			`SELECT item_id, COUNT(*) AS n
			 FROM access_log
			 WHERE arm = ? AND ts > ?
			 GROUP BY item_id
			 ORDER BY n DESC
			 LIMIT ?`
		)
		.bind(arm, since, TOP_N)
		.all<{ item_id: string; n: number }>();

	for (const row of pop.results ?? []) {
		const payload = await readOrigin(env, row.item_id);
		const written = await cachePut(env, arm, row.item_id, payload, 'PREWARM');
		if (written) {
			warmed.push(row.item_id);
			await logPrewarm(env, arm, row.item_id, 'popularity');
		}
	}

	// 2. learn which item tends to follow which, within a session
	const pairs = await env.echocache_logs
		.prepare(
			`WITH seq AS (
			   SELECT session_id, item_id, ts,
			          LEAD(item_id) OVER (PARTITION BY session_id ORDER BY ts) AS next_item
			   FROM access_log
			   WHERE arm = ? AND ts > ?
			 )
			 SELECT item_id, next_item, COUNT(*) AS n
			 FROM seq
			 WHERE next_item IS NOT NULL AND next_item <> item_id
			 GROUP BY item_id, next_item`
		)
		.bind(arm, since)
		.all<{ item_id: string; next_item: string; n: number }>();

	const best: Record<string, { next: string; n: number }> = {};
	for (const row of pairs.results ?? []) {
		const cur = best[row.item_id];
		if (!cur || row.n > cur.n) best[row.item_id] = { next: row.next_item, n: row.n };
	}

	const model: Record<string, string> = {};
	for (const [item, v] of Object.entries(best)) model[item] = v.next;
	await env.CACHE.put(modelKey(arm), JSON.stringify(model), { expirationTtl: 3600 });

	return { warmed, pairs: Object.keys(model).length };
}

export default {
	async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
		const url = new URL(request.url);
		const arm: Arm = url.searchParams.get('arm') === 'off' ? 'off' : 'on';

		// ---- admin endpoints -------------------------------------------------
		if (url.pathname === '/admin/predict') {
			const out = await runPredictor(env, arm);
			return Response.json({ ok: true, arm, ...out });
		}

		if (url.pathname === '/admin/seed') {
			const n = Number(url.searchParams.get('n') ?? '200');
			const stmt = env.echocache_logs.prepare('INSERT OR REPLACE INTO items (item_id, payload) VALUES (?, ?)');
			const batch = [];
			for (let i = 0; i < n; i++) {
				const id = `item_${String(i).padStart(3, '0')}`;
				batch.push(stmt.bind(id, `payload for ${id}`.padEnd(256, '.')));
			}
			await env.echocache_logs.batch(batch);
			return Response.json({ ok: true, seeded: n });
		}

		if (url.pathname === '/admin/reset') {
			await env.echocache_logs.batch([
				env.echocache_logs.prepare('DELETE FROM access_log'),
				env.echocache_logs.prepare('DELETE FROM prewarm_log'),
			]);
			return Response.json({ ok: true, cleared: ['access_log', 'prewarm_log'] });
		}

		if (url.pathname === '/admin/config') {
			return Response.json({ ok: true, soft_ttl_ms: SOFT_TTL_MS, kv_floor_seconds: KV_FLOOR_SECONDS });
		}

		if (url.pathname === '/admin/stats') {
			const r = await env.echocache_logs
				.prepare(
					`SELECT arm, source, COUNT(*) AS n
					 FROM access_log GROUP BY arm, source ORDER BY arm, source`
				)
				.all();
			return Response.json({ ok: true, rows: r.results });
		}

		// ---- the measured request path --------------------------------------
		const itemId = url.searchParams.get('item') ?? 'item_000';
		const sessionId = url.searchParams.get('session') ?? 'anonymous';
		const ts = Date.now();

		const entry = await cacheGet(env, arm, itemId);

		let source: Source;
		let data: string;

		if (fresh(entry)) {
			source = entry.source; // REACTIVE or PREWARM - this is the key measurement
			data = entry.data;
		} else {
			source = 'MISS';
			data = await readOrigin(env, itemId);
			await cachePut(env, arm, itemId, data, 'REACTIVE');
		}

		// treatment arm only: warm whatever usually comes next
		if (arm === 'on') {
			ctx.waitUntil(prewarmSuccessor(env, arm, itemId));
		}

		ctx.waitUntil(
			env.echocache_logs
				.prepare('INSERT INTO access_log (session_id, item_id, ts, cache_hit, arm, source) VALUES (?, ?, ?, ?, ?, ?)')
				.bind(sessionId, itemId, ts, source === 'MISS' ? 0 : 1, arm, source)
				.run()
		);

		return new Response(data, {
			headers: {
				'Content-Type': 'text/plain',
				'X-Cache': source,
				'X-Arm': arm,
			},
		});
	},

	async scheduled(_event: ScheduledEvent, env: Env, _ctx: ExecutionContext): Promise<void> {
		const out = await runPredictor(env, 'on');
		console.log(`pre-warmer: warmed ${out.warmed.length} items, model holds ${out.pairs} successor rules`);
	},
};
