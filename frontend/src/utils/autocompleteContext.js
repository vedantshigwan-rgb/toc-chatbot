/** Build the filters for the CURRENT question, not previous chat messages.
 * AND between variables; repeated values of one variable are OR alternatives.
 * Preserve commas and internal spaces in real business names.
 */
export function normaliseVariable(value) {
  return String(value || "").trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

export function buildAutocompleteFilters({
  text = "", optionValues = {}, reportVariables = [], targetVariable = "",
  reportName = "",
}) {
  const allowed = new Set(
    reportVariables.filter((v) => v?.autocomplete_enabled)
      .map((v) => normaliseVariable(v.variable_name))
  );
  // Docket Report has virtual period filters, while Docket Employee Report
  // has real PERIOD_TYPE and PERIOD_NAME columns.
  if (reportName === "Docket Report") {
    allowed.add("period_type");
    allowed.add("period_name");
  }

  const collected = new Map();
  for (const [variable, value] of Object.entries(optionValues || {})) {
    const key = normaliseVariable(variable);
    const clean = String(value ?? "").trim();
    if (allowed.has(key) && clean) collected.set(key, [clean]);
  }

  // A complete /variable {value} in the current input overrides the sidebar
  // selection for that variable. An incomplete token is never a filter.
  const inline = new Map();
  const pattern = /\/([A-Za-z_][A-Za-z0-9_]*)\s*\{([^{}]*)\}/g;
  for (const match of String(text || "").matchAll(pattern)) {
    const key = normaliseVariable(match[1]);
    const clean = match[2].trim();
    if (!allowed.has(key) || !clean) continue;
    const values = inline.get(key) || [];
    values.push(clean);
    inline.set(key, values);
  }
  for (const [key, values] of inline.entries()) collected.set(key, values);

  collected.delete(normaliseVariable(targetVariable));
  return Object.fromEntries(
    [...collected.entries()].sort(([a], [b]) => a.localeCompare(b))
  );
}
