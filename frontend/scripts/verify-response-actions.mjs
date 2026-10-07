import assert from "node:assert/strict";
import {
  ACTION_BADGES,
  classifyResponseAction,
} from "../src/utils/responseActions.js";

const cases = [
  ["primary reuse is a result", { action: "reuse", workflow_used: "primary" }, "result"],
  ["primary adapt is a result", { action: "adapt", workflow_used: "primary" }, "result"],
  ["greeting is a result", { action: "greeting", workflow_used: "greeting" }, "result"],
  ["help response is a result", { action: "help_response", workflow_used: "help" }, "result"],
  ["metadata response is a result", { action: "metadata_response", workflow_used: "metadata" }, "result"],
  ["report required is controlled", { action: "report_name_required" }, "report_name_required"],
  ["clarification required is non-resumable", { action: "clarification_required" }, "clarification_required"],
  ["invalid feedback target is controlled", { action: "invalid_reply_target" }, "feedback_error"],
  ["missing feedback target is controlled", { action: "reply_to_message_required" }, "feedback_error"],
  ["variable syntax issue is controlled", { action: "fix_variable_syntax" }, "variable_error"],
  ["metadata miss is controlled", { action: "metadata_not_found" }, "metadata_not_found"],
  ["unknown request is controlled", { action: "unknown" }, "unknown"],
  ["retry is controlled", { action: "retry" }, "retry"],
];

for (const [label, payload, expectedKind] of cases) {
  const result = classifyResponseAction(payload);
  assert.equal(result.kind, expectedKind, label);
}

for (const kind of [
  "report_name_required",
  "clarification_required",
  "feedback_error",
  "variable_error",
  "metadata_not_found",
  "unknown",
  "retry",
]) {
  assert.ok(ACTION_BADGES[kind], `missing badge for ${kind}`);
}

console.log(`Verified ${cases.length} current backend action cases.`);
