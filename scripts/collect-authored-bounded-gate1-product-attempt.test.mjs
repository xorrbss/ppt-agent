import assert from "node:assert/strict";
import test from "node:test";

import { validateAttemptEvidenceBindings } from "./collect-authored-bounded-gate1-product-attempt.mjs";

const pins = {
  presentationId: "presentation-1",
  presentationRevision: 3,
  fidelityExportAttemptId: "fidelity-attempt-canonical",
  hybridExportAttemptId: "hybrid-attempt-canonical",
};

function fixture() {
  const epochId = "00000000-0000-4000-8000-000000000041";
  const presentations = ["fidelity", "hybrid"].map((mode) => ({
    ExportMode: mode,
    ExportAttemptId: pins[`${mode}ExportAttemptId`],
    Path: `C:\\evidence\\${mode}-ui.pptx`,
  }));
  const repairPresentations = ["fidelity", "hybrid"].map((mode) => ({
    mode,
    exportAttemptId: pins[`${mode}ExportAttemptId`],
    path: `C:\\evidence\\${mode}-ui.pptx`,
  }));
  const modes = ["fidelity", "hybrid"].map((mode) => ({
    mode,
    exportAttemptId: pins[`${mode}ExportAttemptId`],
    unexpectedPowerPointProcessCount: 0,
    unexpectedPowerPointProcessIds: [],
    launchedPowerPointProcessId: 41,
    cleanupConfirmed: true,
  }));
  return {
    comReport: {
      LaunchedProcessId: 41,
      AttachedApplicationHwnd: 4100,
      AttachedApplicationProcessId: 41,
      ApplicationWindowBindingPhase: "rot-application-hwnd",
      UnexpectedPowerPointProcessCount: 0,
      UnexpectedPowerPointProcessIds: [],
      UnexpectedPowerPointProcesses: [],
      UnexpectedPowerPointWindows: [],
      PowerPointProcessCensusBefore: [],
      PowerPointProcessCensusAfter: [],
      ActivationMethod: "safe-mode-running-object-table",
      ObserverEpoch: { EpochId: epochId, ExpectedProcessId: 41, ActivationMethod: "safe-mode-running-object-table", StartedBeforeActivation: true },
      DiscardedObserverEpochs: [],
      ObservedPowerPointProcesses: [{ EpochId: epochId, ProcessId: 41, ObservationCount: 10 }],
      AttendedWindowEvents: [{ EpochId: epochId, ProcessId: 41, Hwnd: 4100, ClassName: "PPTFrameClass", Visible: true, IsDialog: false }],
      Presentations: presentations,
    },
    repair: { schemaVersion: "authored-powerpoint-attended-repair-observation/v1", authorityDomain: "actual_product", presentationId: pins.presentationId, presentationRevision: pins.presentationRevision, repairDialogObserved: false, windowEvents: [{}], presentations: repairPresentations },
    process: { schemaVersion: "authored-powerpoint-process-census/v1", authorityDomain: "actual_product", presentationId: pins.presentationId, presentationRevision: pins.presentationRevision, observerEpoch: { EpochId: epochId, ExpectedProcessId: 41, ActivationMethod: "safe-mode-running-object-table", StartedBeforeActivation: true }, discardedObserverEpochs: [], modes },
    pins,
  };
}

test("collector binds COM, repair and process evidence to canonical mode attempts", () => {
  const value = fixture();
  assert.deepEqual(Object.keys(validateAttemptEvidenceBindings(value)), ["fidelity", "hybrid"]);
});

test("collector rejects stale attempt replay in every PowerPoint evidence surface", () => {
  for (const mutate of [
    (value) => { value.comReport.Presentations[0].ExportAttemptId = "stale-attempt"; },
    (value) => { value.repair.presentations[0].exportAttemptId = "stale-attempt"; },
    (value) => { value.process.modes[0].exportAttemptId = "stale-attempt"; },
  ]) {
    const value = fixture(); mutate(value);
    assert.throws(() => validateAttemptEvidenceBindings(value), /export-attempt-mismatch:fidelity/);
  }
});

test("collector rejects mode relabel and duplicate replay", () => {
  const relabeled = fixture();
  relabeled.comReport.Presentations[0].ExportMode = "hybrid";
  relabeled.comReport.Presentations[0].ExportAttemptId = pins.hybridExportAttemptId;
  assert.throws(() => validateAttemptEvidenceBindings(relabeled), /mode-invalid:fidelity/);

  const pathRelabeled = fixture();
  pathRelabeled.repair.presentations[0].path = "C:\\evidence\\hybrid-ui.pptx";
  assert.throws(() => validateAttemptEvidenceBindings(pathRelabeled), /canonical-path-mismatch:fidelity/);

  const replayed = fixture();
  replayed.process.modes.push(structuredClone(replayed.process.modes[0]));
  assert.throws(() => validateAttemptEvidenceBindings(replayed), /cardinality-invalid/);
});

test("collector rejects missing, hardcoded or cross-PID COM process guarantees", () => {
  for (const [mutate, expected] of [
    [(value) => { delete value.comReport.UnexpectedPowerPointProcessCount; }, /unexpected-process-guarantee-invalid/],
    [(value) => { value.comReport.AttachedApplicationProcessId = 42; }, /application-process-binding-invalid/],
    [(value) => { delete value.comReport.ApplicationWindowBindingPhase; }, /window-binding-phase-invalid/],
    [(value) => { value.comReport.UnexpectedPowerPointProcessIds = [99]; }, /unexpected-process-guarantee-invalid/],
    [(value) => { value.comReport.PowerPointProcessCensusBefore = [{ ProcessId: 99 }]; }, /opening-final-census-invalid/],
    [(value) => { value.comReport.PowerPointProcessCensusAfter = [{ ProcessId: 41 }]; }, /opening-final-census-invalid/],
    [(value) => { value.comReport.ObservedPowerPointProcesses.push({ ProcessId: 99 }); }, /observed-processes-invalid/],
    [(value) => { value.comReport.AttendedWindowEvents.push({ ProcessId: 99, Hwnd: 9900 }); }, /observed-windows-invalid/],
    [(value) => { value.comReport.AttachedApplicationHwnd = 9999; }, /attached-frame-invalid/],
    [(value) => { delete value.process.observerEpoch; }, /process-census-observer-epoch-mismatch/],
    [(value) => { value.process.modes[0].launchedPowerPointProcessId = 99; }, /process-census-launched-process-mismatch:fidelity/],
  ]) {
    const value = fixture(); mutate(value);
    assert.throws(() => validateAttemptEvidenceBindings(value), expected);
  }
});

test("collector accepts a reset fallback epoch and rejects stale epoch history", () => {
  const value = fixture();
  const oldEpochId = "00000000-0000-4000-8000-000000000040";
  value.comReport.ActivationMethod = "com-create-instance";
  value.comReport.ApplicationWindowBindingPhase = "post-first-open-enumerated-frame-hwnd";
  value.comReport.ObserverEpoch.ActivationMethod = "com-create-instance";
  value.comReport.DiscardedObserverEpochs = [{ EpochId: oldEpochId, Reason: "safe-mode-rot-unavailable", ExpectedProcessId: 40, ObservedProcessIds: [40], UnexpectedPowerPointProcessCount: 0, CleanupConfirmed: true }];
  value.process.observerEpoch = structuredClone(value.comReport.ObserverEpoch);
  value.process.discardedObserverEpochs = structuredClone(value.comReport.DiscardedObserverEpochs);
  assert.deepEqual(Object.keys(validateAttemptEvidenceBindings(value)), ["fidelity", "hybrid"]);

  value.comReport.ObservedPowerPointProcesses.push({ EpochId: oldEpochId, ProcessId: 40 });
  assert.throws(() => validateAttemptEvidenceBindings(value), /observed-processes-invalid|observer-event-epoch-invalid/);
});
