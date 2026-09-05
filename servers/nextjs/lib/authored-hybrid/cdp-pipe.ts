/**
 * Chrome DevTools Protocol pipe transport for the authored-hybrid capture.
 *
 * Split out of `chrome-runner.ts`, which owns the process lifecycle: launching
 * Chrome, profile directories, timeouts and cleanup. This module owns only the
 * wire - framing NUL-delimited CDP JSON over the `--remote-debugging-pipe`
 * file descriptors 3/4, matching responses back to their command ids, and
 * recording every network request Chrome attempted so the caller can fail
 * closed when an authored slide reaches outside its own `data:` payload.
 *
 * The output byte cap lives here because it is the transport's own
 * back-pressure limit: a runaway page must not buffer unbounded CDP output in
 * the Node process. `chrome-runner.ts` reuses the same constant to bound the
 * serialized DOM and screenshot it is willing to accept.
 */
import type { ChildProcess } from "node:child_process";

export const MAX_CAPTURE_OUTPUT_BYTES = 64 * 1024 * 1024;

interface CdpMessage {
  id?: number;
  method?: string;
  sessionId?: string;
  params?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: { message?: string };
}

interface PendingCdpCommand {
  resolve: (result: Record<string, unknown>) => void;
  reject: (error: Error) => void;
}

export class CdpPipe {
  private nextId = 1;
  private input = "";
  private bytes = 0;
  private readonly pending = new Map<number, PendingCdpCommand>();
  private readonly child: ChildProcess;
  readonly blockedRequests: string[] = [];

  constructor(child: ChildProcess) {
    this.child = child;
    const input = child.stdio[4];
    if (!input || typeof input === "number" || !("on" in input)) {
      throw new Error("Chrome did not expose its CDP read pipe.");
    }
    input.on("data", (chunk: Buffer) => this.receive(chunk));
    input.on("error", (error: Error) => this.rejectAll(error));
    child.once("error", (error) => this.rejectAll(error));
    child.once("close", (code, signal) => {
      this.rejectAll(
        new Error(
          `Chrome authored hybrid CDP pipe closed with code ${code ?? "unknown"}${signal ? ` (${signal})` : ""}.`
        )
      );
    });
  }

  private receive(chunk: Buffer): void {
    this.bytes += chunk.length;
    if (this.bytes > MAX_CAPTURE_OUTPUT_BYTES) {
      this.rejectAll(new Error("Chrome authored hybrid CDP output exceeded its byte limit."));
      return;
    }
    this.input += chunk.toString("utf8");
    while (true) {
      const boundary = this.input.indexOf("\0");
      if (boundary < 0) break;
      const raw = this.input.slice(0, boundary);
      this.input = this.input.slice(boundary + 1);
      if (!raw) continue;
      let message: CdpMessage;
      try {
        message = JSON.parse(raw) as CdpMessage;
      } catch (error) {
        this.rejectAll(new Error("Chrome returned malformed CDP JSON.", { cause: error }));
        continue;
      }
      if (message.id !== undefined) {
        const pending = this.pending.get(message.id);
        if (!pending) continue;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message ?? "CDP command failed."));
        else pending.resolve(message.result ?? {});
        continue;
      }
      if (message.method === "Fetch.requestPaused" && message.params) {
        const request = message.params.request as { url?: string } | undefined;
        const requestId = message.params.requestId;
        const url = request?.url ?? "unknown";
        if (typeof requestId === "string") {
          if (/^(?:data:|about:blank$)/i.test(url)) {
            void this.send(
              "Fetch.continueRequest",
              { requestId },
              message.sessionId
            ).catch(() => {});
          } else {
            this.blockedRequests.push(url);
            void this.send(
              "Fetch.failRequest",
              { requestId, errorReason: "BlockedByClient" },
              message.sessionId
            ).catch(() => {});
          }
        }
      }
      if (message.method === "Network.requestWillBeSent" && message.params) {
        const request = message.params.request as { url?: string } | undefined;
        const url = request?.url ?? "unknown";
        if (!/^(?:data:|about:blank$)/i.test(url) && !this.blockedRequests.includes(url)) {
          this.blockedRequests.push(url);
        }
      }
    }
  }

  private rejectAll(error: Error): void {
    for (const command of this.pending.values()) command.reject(error);
    this.pending.clear();
  }

  send(
    method: string,
    params: Record<string, unknown> = {},
    sessionId?: string
  ): Promise<Record<string, unknown>> {
    const output = this.child.stdio[3];
    if (!output || typeof output === "number" || !("write" in output)) {
      return Promise.reject(new Error("Chrome did not expose its CDP write pipe."));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      output.write(`${JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })}\0`, (error?: Error | null) => {
        if (!error) return;
        this.pending.delete(id);
        reject(error);
      });
    });
  }
}

export function stringResult(result: Record<string, unknown>): string | undefined {
  const remote = result.result as { value?: unknown } | undefined;
  return typeof remote?.value === "string" ? remote.value : undefined;
}
