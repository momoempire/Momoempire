export const fmtUSD = (n) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(n) || 0);
export const lineTotal = (l) => (Number(l?.quantity) || 1) * (Number(l?.unit_price) || 0);
