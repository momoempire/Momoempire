// EMP-W-CF-022: a quote's public /q/ link is only worth sharing when the customer can act on it:
// the owner approved it (the accept endpoint requires that) and it is no longer a draft (drafts
// are not public). Dashboard estimates become owner-approved when they are sent (POST .../send).
export function canShareQuoteLink(estimate) {
  return Boolean(estimate && estimate.owner_approved) && estimate.status !== "draft";
}
