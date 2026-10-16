/** Normalize paging metadata across the existing Redis and contextual endpoints. */
export function normaliseSuggestionPage(payload, offset = 0) {
  const values = Array.isArray(payload?.values)
    ? payload.values.map(String)
    : [];
  const total = Number(payload?.total_matches);
  const explicitHasMore = typeof payload?.has_more === "boolean"
    ? payload.has_more
    : Number.isFinite(total) && total >= 0
      ? offset + values.length < total
      : false;
  const proposedOffset = Number(payload?.next_offset);
  const nextOffset = explicitHasMore
    ? Number.isInteger(proposedOffset) && proposedOffset > offset
      ? proposedOffset
      : offset + values.length
    : null;
  return {
    values,
    hasMore: Boolean(explicitHasMore && nextOffset > offset),
    nextOffset: explicitHasMore && nextOffset > offset ? nextOffset : null,
  };
}
