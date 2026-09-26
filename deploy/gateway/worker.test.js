// Tests for deploy/gateway/worker.js - Run with: node --test deploy/gateway/worker.test.js
import { test } from "node:test";
import assert from "node:assert";
import worker from "./worker.js";

test("RunPod mode: moves caller's Authorization to X-Pravrudhi-Authorization", async () => {
  const env = {
    KV: { get: async () => "https://engine.example.com" },
    ENGINE_KEY: "engine_url",
    EDITION: "product",
    UPSTREAM_KIND: "runpod",
    RUNPOD_API_KEY: "runpod-key-secret",
    PRAVRUDHI_VERSION: "0.5.29",
  };

  const request = new Request("https://worker.example.com/api/health", {
    method: "GET",
    headers: {
      "authorization": "Bearer user-token-abc123",
    },
  });

  let capturedRequest = null;
  global.fetch = async (url, options) => {
    capturedRequest = { url, options };
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  await worker.fetch(request, env);

  assert.notStrictEqual(capturedRequest, null);
  assert.strictEqual(capturedRequest.options.headers.get("authorization"), "Bearer runpod-key-secret");
  assert.strictEqual(capturedRequest.options.headers.get("x-pravrudhi-authorization"), "Bearer user-token-abc123");
});

test("RunPod mode: does not expose RunPod API key in response", async () => {
  const env = {
    KV: { get: async () => "https://engine.example.com" },
    ENGINE_KEY: "engine_url",
    EDITION: "product",
    UPSTREAM_KIND: "runpod",
    RUNPOD_API_KEY: "runpod-key-secret",
    PRAVRUDHI_VERSION: "0.5.29",
  };

  const request = new Request("https://worker.example.com/api/health");

  global.fetch = async () => {
    return new Response(JSON.stringify({ version: "0.5.29" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  const response = await worker.fetch(request, env);
  const body = await response.text();
  assert(!body.includes("runpod-key-secret"));
});

test("RunPod mode: sets User-Agent header when not provided", async () => {
  const env = {
    KV: { get: async () => "https://engine.example.com" },
    ENGINE_KEY: "engine_url",
    EDITION: "product",
    UPSTREAM_KIND: "runpod",
    RUNPOD_API_KEY: "runpod-key-secret",
    PRAVRUDHI_VERSION: "0.5.29",
  };

  const request = new Request("https://worker.example.com/api/health");

  let capturedRequest = null;
  global.fetch = async (url, options) => {
    capturedRequest = { url, options };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  await worker.fetch(request, env);

  assert.strictEqual(capturedRequest.options.headers.get("user-agent"), "pravrudhi/0.5.29");
});

test("RunPod mode: preserves User-Agent header when already provided", async () => {
  const env = {
    KV: { get: async () => "https://engine.example.com" },
    ENGINE_KEY: "engine_url",
    EDITION: "product",
    UPSTREAM_KIND: "runpod",
    RUNPOD_API_KEY: "runpod-key-secret",
    PRAVRUDHI_VERSION: "0.5.29",
  };

  const request = new Request("https://worker.example.com/api/health", {
    headers: { "user-agent": "Custom/1.0" },
  });

  let capturedRequest = null;
  global.fetch = async (url, options) => {
    capturedRequest = { url, options };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  await worker.fetch(request, env);

  assert.strictEqual(capturedRequest.options.headers.get("user-agent"), "Custom/1.0");
});

test("Client-IP proof: forwards CF-Connecting-IP and the shared secret when both are configured", async () => {
  const env = {
    KV: { get: async () => "https://engine.example.com" },
    ENGINE_KEY: "engine_url",
    EDITION: "product",
    PRAVRUDHI_VERSION: "0.5.29",
    CLIENT_IP_SECRET: "worker-secret-abc",
  };

  const request = new Request("https://worker.example.com/api/v1/analyse-facts", {
    method: "POST",
    headers: { "cf-connecting-ip": "203.0.113.7" },
  });

  let capturedRequest = null;
  global.fetch = async (url, options) => {
    capturedRequest = { url, options };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  await worker.fetch(request, env);

  assert.strictEqual(capturedRequest.options.headers.get("x-pravrudhi-client-ip"), "203.0.113.7");
  assert.strictEqual(capturedRequest.options.headers.get("x-pravrudhi-client-ip-secret"), "worker-secret-abc");
});

test("Client-IP proof: a caller-supplied client-IP header is discarded, never merged", async () => {
  const env = {
    KV: { get: async () => "https://engine.example.com" },
    ENGINE_KEY: "engine_url",
    EDITION: "product",
    PRAVRUDHI_VERSION: "0.5.29",
    CLIENT_IP_SECRET: "worker-secret-abc",
  };

  // A caller cannot reach Cloudflare's own CF-Connecting-IP, but CAN set these two headers directly if it
  // hits the Worker without going through Cloudflare's edge in front of it -- the Worker must overwrite
  // both, unconditionally, not merge with whatever arrived.
  const request = new Request("https://worker.example.com/api/v1/analyse-facts", {
    method: "POST",
    headers: {
      "cf-connecting-ip": "203.0.113.7",
      "x-pravrudhi-client-ip": "1.2.3.4",
      "x-pravrudhi-client-ip-secret": "not-the-real-secret",
    },
  });

  let capturedRequest = null;
  global.fetch = async (url, options) => {
    capturedRequest = { url, options };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  await worker.fetch(request, env);

  assert.strictEqual(capturedRequest.options.headers.get("x-pravrudhi-client-ip"), "203.0.113.7");
  assert.strictEqual(capturedRequest.options.headers.get("x-pravrudhi-client-ip-secret"), "worker-secret-abc");
});

test("Client-IP proof: no CLIENT_IP_SECRET configured means neither header is ever sent", async () => {
  const env = {
    KV: { get: async () => "https://engine.example.com" },
    ENGINE_KEY: "engine_url",
    EDITION: "product",
    PRAVRUDHI_VERSION: "0.5.29",
    // CLIENT_IP_SECRET deliberately absent: this deployment hasn't been given the Worker's half of the
    // secret yet, so it must fail closed to "send nothing" rather than forward an unproven client-IP.
  };

  const request = new Request("https://worker.example.com/api/v1/analyse-facts", {
    method: "POST",
    headers: {
      "cf-connecting-ip": "203.0.113.7",
      "x-pravrudhi-client-ip": "1.2.3.4",
      "x-pravrudhi-client-ip-secret": "whatever",
    },
  });

  let capturedRequest = null;
  global.fetch = async (url, options) => {
    capturedRequest = { url, options };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  await worker.fetch(request, env);

  assert.strictEqual(capturedRequest.options.headers.get("x-pravrudhi-client-ip"), null);
  assert.strictEqual(capturedRequest.options.headers.get("x-pravrudhi-client-ip-secret"), null);
});

test("Non-RunPod mode: forwards headers unchanged", async () => {
  const env = {
    KV: { get: async () => "https://engine.example.com" },
    ENGINE_KEY: "engine_url",
    EDITION: "product",
    PRAVRUDHI_VERSION: "0.5.29",
  };

  const request = new Request("https://worker.example.com/api/health", {
    headers: { "authorization": "Bearer user-token-abc123" },
  });

  let capturedRequest = null;
  global.fetch = async (url, options) => {
    capturedRequest = { url, options };
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };

  await worker.fetch(request, env);

  assert.strictEqual(capturedRequest.options.headers.get("authorization"), "Bearer user-token-abc123");
  assert.strictEqual(capturedRequest.options.headers.get("x-pravrudhi-authorization"), null);
});
