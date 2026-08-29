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

export function defaultExportProcessWeightLimit(
  cpuCount: number = cpus().length,
): number {
  return Math.min(12, Math.max(2, cpuCount - 2));
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
