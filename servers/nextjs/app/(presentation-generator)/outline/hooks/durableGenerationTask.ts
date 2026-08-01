import type {
  GenerationFailure,
  GenerationProgress,
} from "../../services/api/presentation-generation";

export type DurableGenerationMode = "authored" | "template-v2";

export type DurableGenerationStatus =
  | "starting"
  | "pending"
  | "running"
  | "background"
  | "retryable-error";

export interface DurableGenerationTask {
  version: 1;
  idempotencyKey: string;
  taskId: string | null;
  mode: DurableGenerationMode;
  sourcePresentationId: string | null;
  requestId?: string;
  status: DurableGenerationStatus;
  createdAt: number;
  deadlineAt: number;
  progressDurationSeconds: number;
  lastProgress?: GenerationProgress;
  lastFailure?: GenerationFailure;
}

const STORAGE_KEY = "presenton:durable-generation-tasks:v1";
const TASK_CHANGE_EVENT = "presenton:durable-generation-task-change";

type DurableGenerationStore = Record<string, DurableGenerationTask>;

const hasBrowserStorage = () => typeof window !== "undefined";

const readStore = (): DurableGenerationStore => {
  if (!hasBrowserStorage()) return {};
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed as DurableGenerationStore;
  } catch {
    return {};
  }
};

const writeStore = (store: DurableGenerationStore) => {
  if (!hasBrowserStorage()) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
    window.dispatchEvent(new Event(TASK_CHANGE_EVENT));
  } catch (error) {
    // Generation can still proceed when storage is unavailable (private mode,
    // quota, or a locked-down embedded browser); it simply cannot be resumed.
    console.warn("Unable to persist presentation generation task.", error);
  }
};

const createRandomId = () => {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
};

export const createDurableGenerationTask = ({
  mode,
  sourcePresentationId,
  timeoutMs,
  progressDurationSeconds,
}: {
  mode: DurableGenerationMode;
  sourcePresentationId: string | null;
  timeoutMs: number;
  progressDurationSeconds: number;
}): DurableGenerationTask => {
  const createdAt = Date.now();
  return {
    version: 1,
    idempotencyKey: `ppt-generation-${createRandomId()}`,
    taskId: null,
    mode,
    sourcePresentationId,
    status: "starting",
    createdAt,
    deadlineAt: createdAt + timeoutMs,
    progressDurationSeconds,
  };
};

export const saveDurableGenerationTask = (task: DurableGenerationTask) => {
  const store = readStore();
  store[task.idempotencyKey] = task;
  writeStore(store);
  return task;
};

export const updateDurableGenerationTask = (
  task: DurableGenerationTask,
  patch: Partial<DurableGenerationTask>
) => saveDurableGenerationTask({ ...task, ...patch, version: 1 });

export const findDurableGenerationTask = (
  sourcePresentationId: string | null
): DurableGenerationTask | null => {
  if (!sourcePresentationId) return null;
  const tasks = Object.values(readStore()).filter(
    (task) =>
      task?.version === 1 &&
      task.sourcePresentationId === sourcePresentationId &&
      typeof task.idempotencyKey === "string"
  );
  return tasks.sort((left, right) => right.createdAt - left.createdAt)[0] || null;
};

export const clearDurableGenerationTask = (idempotencyKey: string) => {
  const store = readStore();
  if (!(idempotencyKey in store)) return;
  delete store[idempotencyKey];
  writeStore(store);
};

export const subscribeToDurableGenerationTasks = (
  listener: () => void
) => {
  if (!hasBrowserStorage()) return () => undefined;
  const handleStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) listener();
  };
  window.addEventListener(TASK_CHANGE_EVENT, listener);
  window.addEventListener("storage", handleStorage);
  return () => {
    window.removeEventListener(TASK_CHANGE_EVENT, listener);
    window.removeEventListener("storage", handleStorage);
  };
};
