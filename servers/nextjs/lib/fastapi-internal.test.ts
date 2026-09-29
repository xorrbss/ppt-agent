import assert from "node:assert/strict";
import test from "node:test";

import { getFastApiBaseUrl } from "./fastapi-internal.ts";

const KEYS = ["FAST_API_INTERNAL_URL", "NEXT_PUBLIC_FAST_API"] as const;

function withEnv(values: Partial<Record<(typeof KEYS)[number], string>>, run: () => void) {
  const saved = KEYS.map((key) => [key, process.env[key]] as const);
  for (const key of KEYS) delete process.env[key];
  Object.assign(process.env, values);
  try {
    run();
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

test("getFastApiBaseUrl prefers FAST_API_INTERNAL_URL over NEXT_PUBLIC_FAST_API", () => {
  withEnv(
    { FAST_API_INTERNAL_URL: "http://internal:8000/", NEXT_PUBLIC_FAST_API: "http://public:5000" },
    () => assert.equal(getFastApiBaseUrl(), "http://internal:8000"),
  );
});

test("getFastApiBaseUrl falls back to NEXT_PUBLIC_FAST_API, then the loopback default", () => {
  withEnv({ NEXT_PUBLIC_FAST_API: " http://public:5000// " }, () =>
    assert.equal(getFastApiBaseUrl(), "http://public:5000"),
  );
  withEnv({ FAST_API_INTERNAL_URL: "   " }, () =>
    assert.equal(getFastApiBaseUrl(), "http://127.0.0.1:8000"),
  );
  withEnv({}, () => assert.equal(getFastApiBaseUrl(), "http://127.0.0.1:8000"));
});
