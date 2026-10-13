export interface Env {
  // These bindings must match the names in your wrangler.jsonc exactly
  echocache_logs: D1Database;
  CACHE: KVNamespace;
}

export default {
  // 1. THE API ENDPOINT (Runs when a user requests data)
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const itemId = url.searchParams.get("item") || "default-item";
    const sessionId = url.searchParams.get("session") || "anonymous";
    const ts = Date.now();

    // Check if item is in the cache (the "shelf")
    const cachedData = await env.CACHE.get(itemId);
    const cacheHit = cachedData !== null ? 1 : 0;

    let responseData = cachedData;

    if (!cacheHit) {
      // Cache miss: simulate fetching from the slow backend (the "storeroom")
      responseData = `Data for ${itemId}`;
      // Write it to the cache so the next normal request gets it
      await env.CACHE.put(itemId, responseData, { expirationTtl: 60 });
    }

    // Log the request to the D1 database in the background (doesn't slow down the user response)
    ctx.waitUntil(
      env.echocache_logs.prepare(
        "INSERT INTO access_log (session_id, item_id, ts, cache_hit) VALUES (?, ?, ?, ?)"
      ).bind(sessionId, itemId, ts, cacheHit).run()
    );

    return new Response(
      `Item: ${itemId}\nStatus: ${cacheHit ? "CACHE HIT (Fast)" : "CACHE MISS (Slow)"}\nData: ${responseData}`,
      { headers: { "Content-Type": "text/plain" } }
    );
  },

  // 2. THE PRE-WARMER (Runs every 5 minutes based on our Cron Trigger)
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    console.log("⏰ Running predictive pre-warmer...");
    
    // Simple Prediction Logic: Find the top 3 most requested items in our logs
    const { results } = await env.echocache_logs.prepare(`
      SELECT item_id, COUNT(*) as hits 
      FROM access_log 
      GROUP BY item_id 
      ORDER BY hits DESC 
      LIMIT 3
    `).all();

    if (results && results.length > 0) {
      for (const row of results) {
        const itemId = row.item_id as string;
        console.log(`Pre-warming popular item: ${itemId}`);
        
        // Pre-load it into KV ahead of time!
        await env.CACHE.put(itemId, `Data for ${itemId}`, { expirationTtl: 300 });
      }
      console.log("✅ Pre-warming complete.");
    } else {
      console.log("No log data found. Skipping pre-warming.");
    }
  }
};