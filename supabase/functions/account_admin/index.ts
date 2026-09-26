// Privileged credentials stay in the hosted function. Nothing imports this into Flask.
const base = Deno.env.get("SUPABASE_URL")!;
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const publicKey = Deno.env.get("SUPABASE_ANON_KEY")!;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const reply = (status: number, data: unknown) => new Response(JSON.stringify(data), {
  status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
});

async function api(path: string, key: string, bearer: string, body?: unknown) {
  return await fetch(base + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { apikey: key, Authorization: bearer, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(10000), redirect: "error",
  });
}

async function recoverAccount(email: string, requestId: string): Promise<string | null> {
  for (let page = 1; page <= 20; page++) {
    const response = await api(`/auth/v1/admin/users?page=${page}&per_page=200`,
      serviceKey, `Bearer ${serviceKey}`);
    if (!response.ok) throw new Error("Auth unavailable");
    const users = (await response.json()).users;
    for (const candidate of users) {
      if (candidate.email?.toLowerCase() === email
          && candidate.app_metadata?.algoguard_request_id === requestId) return candidate.id;
    }
    if (users.length < 200) return null;
  }
  throw new Error("Recovery scan limit reached");
}

Deno.serve(async (request: Request) => {
  if (request.method !== "POST") return reply(405, { error: "method_not_allowed" });
  const bearer = request.headers.get("Authorization") ?? "";
  if (!bearer.startsWith("Bearer ")) return reply(401, { error: "authentication_required" });
  try {
    // Online Auth verification supports ES256 and refuses forged/expired tokens.
    const identity = await api("/auth/v1/user", publicKey, bearer);
    if (!identity.ok) return reply(401, { error: "authentication_required" });
    const user = await identity.json();
    const allowed = await api("/rest/v1/rpc/administration_authorized", publicKey, bearer, {});
    if (!allowed.ok || await allowed.json() !== true) return reply(403, { error: "access_denied" });

    // Limit even chunked bodies before parsing; do not log request bodies or credentials.
    const reader = request.body?.getReader();
    if (!reader) return reply(400, { error: "invalid_request" });
    const chunks: Uint8Array[] = [];
    let bytes = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.length;
      if (bytes > 8192) { await reader.cancel(); return reply(413, { error: "request_too_large" }); }
      chunks.push(value);
    }
    const raw = new Uint8Array(bytes);
    let offset = 0;
    for (const chunk of chunks) { raw.set(chunk, offset); offset += chunk.length; }
    let body;
    try { body = JSON.parse(new TextDecoder().decode(raw)); }
    catch { return reply(400, { error: "invalid_request" }); }
    if (!body || typeof body !== "object" || Array.isArray(body)
        || typeof body.request_id !== "string" || !uuid.test(body.request_id)) {
      return reply(400, { error: "invalid_request" });
    }
    const { request_id, password, ...command } = body;
    if (command.action === "create_account") {
      if (typeof password !== "string" || password.length < 12 || password.length > 128
          || !/[a-z]/.test(password) || !/[A-Z]/.test(password)
          || !/[0-9]/.test(password) || !/[^a-zA-Z0-9]/.test(password)
          || typeof command.email !== "string" || typeof command.username !== "string") {
        return reply(400, { error: "invalid_account" });
      }
      command.email = command.email.trim().toLowerCase();
      command.username = command.username.trim();
      command.role ??= "analyst";
    }
    const args = { p_actor: user.id, p_request_id: request_id, p_body: command, p_auth_user: null };
    const invoke = () => api("/rest/v1/rpc/administration_command", serviceKey,
      `Bearer ${serviceKey}`, args);
    const sanitize = (status: number) => reply([400, 403, 409].includes(status) ? status : 503,
      { error: status === 403 ? "access_denied" : status === 409 ? "request_conflict"
        : status === 400 ? "invalid_request" : "temporarily_unavailable" });
    let result = await invoke();
    if (!result.ok) return sanitize(result.status);
    let data = await result.json();
    if (command.action !== "create_account" || data.status === "completed") return reply(200, data);

    let authId = await recoverAccount(command.email, request_id);
    if (!authId) {
      const created = await api("/auth/v1/admin/users", serviceKey, `Bearer ${serviceKey}`, {
        email: command.email, password, email_confirm: true,
        app_metadata: { algoguard_request_id: request_id },
      });
      if (created.ok) authId = (await created.json()).id;
      else {
        // Concurrent/lost-response recovery is tied to a server-owned marker.
        result = await invoke();
        if (!result.ok) return sanitize(result.status);
        data = await result.json();
        if (data.status === "completed") return reply(200, data);
        authId = await recoverAccount(command.email, request_id);
        if (!authId) return sanitize([400, 409, 422].includes(created.status) ? 409 : 503);
      }
    }
    args.p_auth_user = authId;
    result = await invoke(); // Recheck current role before creating profile/membership records.
    if (!result.ok) return sanitize(result.status);
    return reply(200, await result.json());
  } catch {
    return reply(503, { error: "temporarily_unavailable" });
  }
});
