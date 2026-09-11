import { cpus } from "node:os";

export interface ExportAdmissionSnapshot {
  readonly capacityWeight: number;
  readonly activeWeight: number;
  readonly activeProcesses: number;
  readonly queuedRequests: number;
  readonly peakActiveWeight: number;
  readonly peakActiveProcesses: number;
  readonly admittedRequests: number;
  readonly totalQueueWaitMs: number;
  readonly maximumQueueWaitMs: number;
}

export interface ExportAdmissionLease {
  readonly queueWaitMs: number;
  readonly activeWeightAtAdmission: number;
  readonly activeProcessesAtAdmission: number;
  release(): void;
}

interface PendingAdmission {
  readonly weight: number;
  readonly queuedAt: number;
  readonly resolve: (lease: ExportAdmissionLease) => void;
}

function positiveInt(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

const MAXIMUM_ADMITTED_PROCESSES = 12;
const MINIMUM_ADMITTED_PROCESSES = 2;

/**
 * Chrome instances this process may run at once for authored export.
 *
 * **Do not lower this to reduce contention.** That was measured on 2026-09-11
 * and is wrong: an authored slide fans out into many captures (a declared
 * pass, a PowerPoint-layout pass, a backplate and one render per raster paint
 * band), so the deck is admission-bound and throughput rises with the limit
 * rather than falling. On a 22-CPU host, the run-11 35-slide deck through the
 * product launch chain gave
 *
 * | limit | wall  | slides attempted | s/slide | peak processes |
 * |------:|------:|-----------------:|--------:|---------------:|
 * |    12 | 1565s |               24 |    65.2 |             81 |
 * |     6 | 1586s |               16 |    99.1 |             40 |
 * |     4 | 1640s |               14 |   117.1 |             29 |
 *
 * Raising it is bounded by something outside this file: an admitted capture
 * costs about 6.75 observed processes, and the isolated export role's Job
 * Object allows 96 (`_ROLE_RESOURCE_BUDGETS["export"]` in
 * `services/trusted_executor/worker_dispatcher.py`). Twelve captures already
 * peak at 81 of those 96, so this ceiling is set by the lane's process budget,
 * not by CPU count. `AUTHORED_EXPORT_PROCESS_WEIGHT_LIMIT` overrides it for a
 * host that has measured something different.
 *
 * See `artifacts/authored-full-product-validation-20260910/run-11-hybrid-export/w17-throughput.md`.
 */
export function defaultExportProcessWeightLimit(
  cpuCount: number = cpus().length,
): number {
  // A container can report 0 or a non-integer CPU count; treat that as the
  // smallest honest machine rather than admitting nothing.
  const usable = Number.isSafeInteger(cpuCount) && cpuCount > 0 ? cpuCount : 1;
  return Math.min(
    MAXIMUM_ADMITTED_PROCESSES,
    Math.max(MINIMUM_ADMITTED_PROCESSES, usable - 2),
  );
}

/**
 * FIFO weighted admission shared by every Authored Chromium process in this
 * Next.js worker. Keeping the admission at the process-launch boundary means
 * Fidelity, Hybrid, isolated raster fallback and PDF fan-out cannot multiply
 * one another's browser count.
 */
export class WeightedExportAdmission {
  readonly capacityWeight: number;
  private activeWeight = 0;
  private activeProcesses = 0;
  private peakActiveWeight = 0;
  private peakActiveProcesses = 0;
  private admittedRequests = 0;
  private totalQueueWaitMs = 0;
  private maximumQueueWaitMs = 0;
  private readonly queue: PendingAdmission[] = [];

  constructor(capacityWeight: number) {
    if (!Number.isSafeInteger(capacityWeight) || capacityWeight <= 0) {
      throw new Error("authored_export_admission_capacity_invalid");
    }
    this.capacityWeight = capacityWeight;
  }

  acquire(weight = 1): Promise<ExportAdmissionLease> {
    if (!Number.isSafeInteger(weight) || weight <= 0 || weight > this.capacityWeight) {
      return Promise.reject(new Error("authored_export_admission_weight_invalid"));
    }
    return new Promise((resolve) => {
      this.queue.push({ weight, queuedAt: performance.now(), resolve });
      this.drain();
    });
  }

  snapshot(): ExportAdmissionSnapshot {
    return {
      capacityWeight: this.capacityWeight,
      activeWeight: this.activeWeight,
      activeProcesses: this.activeProcesses,
      queuedRequests: this.queue.length,
      peakActiveWeight: this.peakActiveWeight,
      peakActiveProcesses: this.peakActiveProcesses,
      admittedRequests: this.admittedRequests,
      totalQueueWaitMs: Math.round(this.totalQueueWaitMs),
      maximumQueueWaitMs: Math.round(this.maximumQueueWaitMs),
    };
  }

  private drain(): void {
    while (this.queue.length) {
      const pending = this.queue[0];
      if (this.activeWeight + pending.weight > this.capacityWeight) return;
      this.queue.shift();
      this.activeWeight += pending.weight;
      this.activeProcesses += 1;
      this.peakActiveWeight = Math.max(this.peakActiveWeight, this.activeWeight);
      this.peakActiveProcesses = Math.max(
        this.peakActiveProcesses,
        this.activeProcesses,
      );
      this.admittedRequests += 1;
      const queueWaitMs = Math.max(0, performance.now() - pending.queuedAt);
      this.totalQueueWaitMs += queueWaitMs;
      this.maximumQueueWaitMs = Math.max(this.maximumQueueWaitMs, queueWaitMs);
      if (queueWaitMs >= 1) {
        console.info(
          `[authored-export-admission] admitted queueWaitMs=${Math.round(queueWaitMs)} ` +
            `activeProcesses=${this.activeProcesses} activeWeight=${this.activeWeight}/${this.capacityWeight}`,
        );
      }
      let released = false;
      pending.resolve({
        queueWaitMs: Math.round(queueWaitMs),
        activeWeightAtAdmission: this.activeWeight,
        activeProcessesAtAdmission: this.activeProcesses,
        release: () => {
          if (released) return;
          released = true;
          this.activeWeight -= pending.weight;
          this.activeProcesses -= 1;
          this.drain();
        },
      });
    }
  }
}

const processAdmission = new WeightedExportAdmission(
  positiveInt(
    process.env.AUTHORED_EXPORT_PROCESS_WEIGHT_LIMIT,
    defaultExportProcessWeightLimit(),
  ),
);

export function acquireAuthoredExportProcess(
  weight = positiveInt(process.env.AUTHORED_EXPORT_CHROME_WEIGHT, 1),
): Promise<ExportAdmissionLease> {
  return processAdmission.acquire(weight);
}

export function authoredExportAdmissionSnapshot(): ExportAdmissionSnapshot {
  return processAdmission.snapshot();
}
