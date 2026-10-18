// Mascot skin catalogue + tier gate (components/mascotSkins.js). The base roster is a
// guy-or-gal choice from the start (Mae & Mo on every plan); Empire adds the three
// premium characters. The marketing page passes no plan → base pair, defaulting to Mae.
const {
  MASCOT_SKINS,
  skinsForPlan,
  isSkinUnlocked,
  resolveSkin,
  DEFAULT_SKIN,
} = require("./mascotSkins");

test("the base roster is a guy-or-gal choice: Mae and Mo on every plan", () => {
  ["lite", "starter", "pro", "", undefined, null].forEach((plan) => {
    expect(skinsForPlan(plan)).toEqual(["mae", "mo"]);
  });
  expect(isSkinUnlocked("mae", "lite")).toBe(true);
  expect(isSkinUnlocked("mo", "lite")).toBe(true);
});

test("Empire unlocks all five skins", () => {
  expect(skinsForPlan("empire")).toEqual(["mae", "mo", "sterling", "sloane", "foreman"]);
  expect(MASCOT_SKINS).toHaveLength(5);
  expect(MASCOT_SKINS.map((s) => s.name)).toEqual(["Mae", "Mo", "Sterling", "Sloane", "The Foreman"]);
});

test("Unlimited inherits the Empire premium skins", () => {
  expect(skinsForPlan("unlimited")).toEqual(["mae", "mo", "sterling", "sloane", "foreman"]);
  expect(resolveSkin("sterling", "unlimited")).toBe("sterling");
  expect(resolveSkin("sloane", "unlimited")).toBe("sloane");
  expect(resolveSkin("foreman", "unlimited")).toBe("foreman");
});

test("premium skins stay locked below Empire", () => {
  ["sterling", "sloane", "foreman"].forEach((id) => {
    expect(isSkinUnlocked(id, "pro")).toBe(false);
    expect(isSkinUnlocked(id, "lite")).toBe(false);
    expect(resolveSkin(id, "starter")).toBe("mae");
  });
});

test("resolveSkin honours allowed requests and defaults to Mae", () => {
  expect(resolveSkin("mo", "lite")).toBe("mo");
  expect(resolveSkin("mo", "")).toBe("mo");
  expect(resolveSkin("sterling", "empire")).toBe("sterling");
  expect(resolveSkin("sloane", "empire")).toBe("sloane");
  expect(resolveSkin("foreman", "empire")).toBe("foreman");
  expect(resolveSkin("", "empire")).toBe(DEFAULT_SKIN);
  expect(resolveSkin("nope", "empire")).toBe("mae");
  expect(resolveSkin("", "")).toBe("mae");
});
