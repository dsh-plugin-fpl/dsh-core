/** Present one live card per application URL while retaining bookmarked registration IDs. */
export function registrationRows<
  T extends { id: string; url: string; package_name: string; status: string },
>(rows: T[]): (T & { registrationIds: string[] })[] {
  const cards: (T & { registrationIds: string[] })[] = [],
    index = new Map<string, number>();
  const rank = (status: string) =>
    status === "ready" ? 2 : status === "offline" ? 1 : 0;
  for (const row of rows) {
    const key = row.url ? row.package_name + "\0" + row.url : "id:" + row.id;
    const position = index.get(key);
    if (position === undefined) {
      index.set(key, cards.length);
      cards.push({ ...row, registrationIds: [row.id] });
      continue;
    }
    const current = cards[position],
      registrationIds = [...current.registrationIds, row.id];
    cards[position] =
      rank(row.status) > rank(current.status)
        ? { ...row, registrationIds }
        : { ...current, registrationIds };
  }
  return cards;
}
