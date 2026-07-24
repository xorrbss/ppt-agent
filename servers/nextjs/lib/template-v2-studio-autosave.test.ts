import assert from "node:assert/strict";
import test from "node:test";

import {
  createTemplateV2AutosaveScheduler,
  type TemplateV2AutosaveTimers,
  type TemplateV2AutosaveTrigger,
} from "./template-v2-studio-autosave.ts";

class ManualTimers implements TemplateV2AutosaveTimers {
  private nextId = 1;
  private callbacks = new Map<number, () => void>();

  setTimeout(callback: () => void): number {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    return id;
  }

  clearTimeout(handle: unknown): void {
    this.callbacks.delete(handle as number);
  }

  fireAll(): void {
    const callbacks = [...this.callbacks.values()];
    this.callbacks.clear();
    callbacks.forEach((callback) => callback());
  }

  get size(): number {
    return this.callbacks.size;
  }
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

test("debounce retains only the newest immutable snapshot", async () => {
  const timers = new ManualTimers();
  const saved: string[] = [];
  const scheduler = createTemplateV2AutosaveScheduler({
    debounceMs: 500,
    timers,
    async save(snapshot: string) {
      saved.push(snapshot);
    },
  });

  scheduler.schedule("first");
  scheduler.schedule("second");

  assert.equal(timers.size, 1);
  assert.deepEqual(saved, []);
  timers.fireAll();
  await settle();
  assert.deepEqual(saved, ["second"]);
  assert.deepEqual(scheduler.getState(), {
    pending: false,
    inFlight: false,
    blockedByError: false,
    closing: false,
    disposed: false,
  });
});

test("single-flight save queues a debounced edit and follows up after success", async () => {
  const timers = new ManualTimers();
  const first = deferred<number>();
  const second = deferred<number>();
  const calls: Array<{ snapshot: string; trigger: TemplateV2AutosaveTrigger }> =
    [];
  const savedRevisions: number[] = [];
  const scheduler = createTemplateV2AutosaveScheduler({
    debounceMs: 250,
    timers,
    save(snapshot: string, context) {
      calls.push({ snapshot, trigger: context.trigger });
      return calls.length === 1 ? first.promise : second.promise;
    },
    onSaved(_snapshot, revision) {
      savedRevisions.push(revision);
    },
  });

  scheduler.schedule("revision-1");
  timers.fireAll();
  await settle();
  scheduler.schedule("revision-2");
  timers.fireAll();
  await settle();

  assert.deepEqual(calls, [
    { snapshot: "revision-1", trigger: "debounce" },
  ]);
  first.resolve(2);
  await settle();
  assert.deepEqual(calls, [
    { snapshot: "revision-1", trigger: "debounce" },
    { snapshot: "revision-2", trigger: "queued" },
  ]);
  second.resolve(3);
  await settle();
  assert.deepEqual(savedRevisions, [2, 3]);
  assert.equal(scheduler.getState().inFlight, false);
});

test("re-scheduling the active immutable snapshot does not duplicate a save", async () => {
  const request = deferred<void>();
  const snapshot = { revision: 1 };
  let calls = 0;
  const scheduler = createTemplateV2AutosaveScheduler({
    debounceMs: 250,
    async save() {
      calls += 1;
      await request.promise;
    },
  });

  scheduler.schedule(snapshot);
  const firstFlush = scheduler.flush();
  await settle();
  scheduler.schedule(snapshot);
  const secondFlush = scheduler.flush();

  assert.equal(calls, 1);
  assert.equal(scheduler.getState().pending, false);
  request.resolve();
  await Promise.all([firstFlush, secondFlush]);
  assert.equal(calls, 1);
});

test("flush bypasses debounce and waits for edits queued during a save", async () => {
  const timers = new ManualTimers();
  const first = deferred<void>();
  const calls: string[] = [];
  const scheduler = createTemplateV2AutosaveScheduler({
    debounceMs: 1_000,
    timers,
    async save(snapshot: string) {
      calls.push(snapshot);
      if (snapshot === "first") await first.promise;
    },
  });

  scheduler.schedule("first");
  const flushing = scheduler.flush();
  await settle();
  scheduler.schedule("second");
  await settle();
  assert.deepEqual(calls, ["first"]);

  first.resolve();
  await flushing;
  assert.deepEqual(calls, ["first", "second"]);
  assert.equal(timers.size, 0);
  assert.equal(scheduler.getState().pending, false);
});

test("a failure is retained, reported, and never automatically retried", async () => {
  const timers = new ManualTimers();
  const persistenceError = new Error("network unavailable");
  const errors: unknown[] = [];
  const successes: string[] = [];
  const calls: string[] = [];
  let fail = true;
  const scheduler = createTemplateV2AutosaveScheduler({
    debounceMs: 100,
    timers,
    async save(snapshot: string) {
      calls.push(snapshot);
      if (fail) throw persistenceError;
      return "saved";
    },
    onSaved(snapshot) {
      successes.push(snapshot);
    },
    onError(error) {
      errors.push(error);
    },
  });

  scheduler.schedule("failed");
  await assert.rejects(scheduler.flush(), persistenceError);
  assert.deepEqual(errors, [persistenceError]);
  assert.deepEqual(successes, []);
  assert.deepEqual(calls, ["failed"]);
  assert.equal(scheduler.getState().blockedByError, true);
  assert.equal(scheduler.getState().pending, true);

  scheduler.schedule("newest");
  assert.equal(timers.size, 0);
  await settle();
  assert.deepEqual(calls, ["failed"]);

  fail = false;
  scheduler.resume();
  assert.equal(timers.size, 1);
  timers.fireAll();
  await settle();
  assert.deepEqual(calls, ["failed", "newest"]);
  assert.deepEqual(successes, ["newest"]);
  assert.equal(scheduler.getState().blockedByError, false);
});

test("an opaque revision conflict is delegated without losing a newer edit", async () => {
  class RevisionConflict extends Error {
    readonly status = 409;
  }

  const timers = new ManualTimers();
  const conflict = new RevisionConflict("revision conflict");
  const first = deferred<void>();
  const observed: unknown[] = [];
  const calls: string[] = [];
  const scheduler = createTemplateV2AutosaveScheduler({
    debounceMs: 100,
    timers,
    async save(snapshot: string) {
      calls.push(snapshot);
      if (snapshot === "server-snapshot") {
        await first.promise;
        throw conflict;
      }
    },
    onError(error) {
      observed.push(error);
    },
  });

  scheduler.schedule("server-snapshot");
  timers.fireAll();
  await settle();
  scheduler.schedule("local-after-request");
  timers.fireAll();
  first.resolve();
  await settle();

  assert.deepEqual(observed, [conflict]);
  assert.deepEqual(calls, ["server-snapshot"]);
  assert.equal(scheduler.getState().pending, true);
  assert.equal(scheduler.getState().blockedByError, true);

  scheduler.discardPending();
  assert.deepEqual(scheduler.getState(), {
    pending: false,
    inFlight: false,
    blockedByError: false,
    closing: false,
    disposed: false,
  });
});

test("dispose flushes by default, rejects new edits, and never aborts its request", async () => {
  const timers = new ManualTimers();
  const request = deferred<void>();
  const triggers: TemplateV2AutosaveTrigger[] = [];
  const scheduler = createTemplateV2AutosaveScheduler({
    debounceMs: 1_000,
    timers,
    async save(_snapshot: string, context) {
      triggers.push(context.trigger);
      await request.promise;
    },
  });

  scheduler.schedule("navigation-snapshot");
  const disposal = scheduler.dispose();
  await settle();

  assert.deepEqual(triggers, ["dispose"]);
  assert.equal(scheduler.schedule("too-late"), false);
  assert.equal(scheduler.getState().closing, true);
  request.resolve();
  await disposal;
  assert.deepEqual(scheduler.getState(), {
    pending: false,
    inFlight: false,
    blockedByError: false,
    closing: false,
    disposed: true,
  });
});

test("dispose without flush abandons only debounce-pending work", async () => {
  const timers = new ManualTimers();
  const activeRequest = deferred<void>();
  const calls: string[] = [];
  const scheduler = createTemplateV2AutosaveScheduler({
    debounceMs: 100,
    timers,
    async save(snapshot: string) {
      calls.push(snapshot);
      await activeRequest.promise;
    },
  });

  scheduler.schedule("active");
  timers.fireAll();
  await settle();
  scheduler.schedule("pending");
  const disposal = scheduler.dispose({ flush: false });
  await settle();

  assert.deepEqual(calls, ["active"]);
  assert.equal(scheduler.getState().inFlight, true);
  activeRequest.resolve();
  await disposal;
  assert.deepEqual(calls, ["active"]);
  assert.equal(scheduler.getState().disposed, true);
});
