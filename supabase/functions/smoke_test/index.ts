// supabase/functions/smoke_test/index.ts
//
// Minimal Edge Function that exists solely to verify Stage 5A.3's
// requirement that local Edge Functions work. It does nothing
// application-specific and is not meant to be deployed to the cloud
// pilot project. Safe to delete once 5A.3 evidence is recorded, or keep
// as a lightweight health check.

Deno.serve(async (_req: Request) => {
  return new Response(
    JSON.stringify({ message: "ok", source: "local-smoke-test" }),
    { headers: { "Content-Type": "application/json" } },
  );
});