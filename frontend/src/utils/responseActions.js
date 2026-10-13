const ACTION_KINDS = {
  report_name_required: "report_name_required",
  report_change_not_allowed: "report_locked",
  feedback_report_mismatch: "report_locked",
  clarification_required: "clarification_required",
  invalid_reply_target: "feedback_error",
  reply_to_message_required: "feedback_error",
  fix_variable_syntax: "variable_error",
  metadata_not_found: "metadata_not_found",
  unknown: "unknown",
  retry: "retry",
};

export const ACTION_BADGES = {
  report_name_required: "Select a report",
  report_locked: "Report locked",
  clarification_required: "Clarification needed",
  feedback_error: "Cannot modify result",
  variable_error: "Fix variable input",
  metadata_not_found: "Report term not found",
  unknown: "Request not understood",
  retry: "Try again",
};

export function classifyResponseAction(responseData) {
  const action = responseData?.action || null;
  const kind = ACTION_KINDS[action];

  if (kind) {
    return { kind, action };
  }

  return { kind: "result", action };
}