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
