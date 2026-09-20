"use strict";
const $ = (id) => document.getElementById(id);
const families = {
  default: "Automatic",
  sph: "SPH",
  spf: "SPF",
  spa: "SPA",
  mod: "MOD",
  min: "MIN",
  tl3: "TL3",
  max: "MAX",
};
const models = {
  auto: "Standard controls",
  sph: "SPH battery",
  spa: "SPA battery",
  min_tl_xh: "MIN TL-XH battery",
  mod_tl3_xh: "MOD / MID TL3-XH battery",
};
let expected = null;
let selectionRevision = 0;
function invalidatePeriod() {
  expected = null;
  selectionRevision++;
  $("save-slot").disabled = true;
}
function node(tag, text) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  return el;
}
function select(label, choices, value) {
  const wrap = node("label", label),
    el = node("select");
  for (const [key, text] of Object.entries(choices)) {
    const option = node("option", text);
    option.value = key;
    el.append(option);
  }
  el.value = value;
  wrap.append(el);
  return [wrap, el];
}
async function api(path, data) {
  const response = await fetch(
    path,
    data === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-HA-Growatt": "1" },
          body: JSON.stringify(data),
        },
  );
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "The request failed.");
  return result;
}
async function action(work) {
  try {
    await work();
  } catch (error) {
    $("message").textContent = error.message;
  }
}
async function refresh() {
  invalidatePeriod();
  const data = await api("api/status");
  $("checks").replaceChildren();
  for (const [label, value] of [
    ["Datalogger listener", data.listener ? "Listening" : "Stopped"],
    ["MQTT broker", data.mqtt_connected ? "Connected" : "Disconnected"],
    [
      "Restart recovery",
      data.recovery_healthy
        ? "Available"
        : data.recovery_enabled
          ? "Needs attention"
          : "Disabled",
    ],
  ]) {
    const card = node("div", label);
    card.className = "check";
    card.append(node("strong", value));
    $("checks").append(card);
  }
  $("warnings").replaceChildren(
    ...data.warnings.map((text) => node("li", text)),
  );
  $("devices").replaceChildren();
  const previous = $("schedule-device").value;
  $("schedule-device").replaceChildren();
  for (const device of data.devices) {
    const section = node("div");
    section.className = "device";
    section.append(
      node("h3", device.identity),
      node(
        "p",
        `${device.restored ? "Saved readings; waiting for fresh data" : device.recent ? "Recent readings" : "No recent readings"} · ${device.profile} · ${device.connection}`,
      ),
    );
    const form = node("form"),
      fields = node("div");
    fields.className = "fields";
    const [familyLabel, family] = select(
      "Reading profile",
      families,
      device.family,
    );
    const [modelLabel, model] = select(
      "Control profile",
      models,
      device.controls,
    );
    fields.append(familyLabel, modelLabel);
    const save = node("button", "Save profile");
    form.append(fields, save);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      action(async () => {
        save.disabled = true;
        try {
          const response = await api("api/profiles", {
            serial: device.identity,
            family: family.value,
            controls: model.value,
          });
          $("message").textContent = response.message;
          await refresh();
        } finally {
          save.disabled = false;
        }
      });
    });
    section.append(form);
    $("devices").append(section);
    if (
      ["sph", "spa"].includes(device.controls) &&
      data.experimental_controls
    ) {
      const option = node("option", device.identity);
      option.value = device.identity;
      $("schedule-device").append(option);
    }
  }
  if (!data.devices.length)
    $("devices").append(
      node(
        "p",
        "No inverter has reported yet. Keep this page open and check again after the next upload.",
      ),
    );
  if ([...$("schedule-device").options].some((o) => o.value === previous))
    $("schedule-device").value = previous;
  $("version").textContent = `HA Growatt ${data.version}`;
}
function selection() {
  return {
    serial: $("schedule-device").value,
    mode: $("schedule-mode").value,
    slot: Number($("schedule-slot").value),
  };
}
for (const id of ["schedule-device", "schedule-mode", "schedule-slot"]) {
  $(id).addEventListener("change", () => {
    invalidatePeriod();
    $("schedule-result").textContent =
      "Read the selected period before editing it.";
  });
}
$("read-slot").addEventListener("click", () =>
  action(async () => {
    invalidatePeriod();
    const revision = selectionRevision,
      selected = selection();
    $("read-slot").disabled = true;
    try {
      const data = await api("api/schedule", { ...selected, action: "read" });
      if (
        revision !== selectionRevision ||
        JSON.stringify(selected) !== JSON.stringify(selection())
      )
        return;
      expected = data.period;
      $("start").value = expected.start;
      $("end").value = expected.end;
      $("enabled").checked = expected.enabled;
      $("save-slot").disabled = false;
      $("schedule-result").textContent = "Period read from the inverter.";
    } finally {
      $("read-slot").disabled = false;
    }
  }),
);
$("schedule").addEventListener("submit", (event) => {
  event.preventDefault();
  action(async () => {
    if (!expected) throw Error("Read the selected period before saving it.");
    $("save-slot").disabled = true;
    try {
      const data = await api("api/schedule", {
        ...selection(),
        action: "write",
        expected,
        period: {
          start: $("start").value,
          end: $("end").value,
          enabled: $("enabled").checked,
        },
      });
      expected = data.period;
      $("schedule-result").textContent =
        "Period applied and read back successfully.";
    } finally {
      invalidatePeriod();
      $("schedule-result").textContent +=
        " Read the period again before another change.";
    }
  });
});
$("migration").addEventListener("click", () =>
  action(async () => {
    $("migration").disabled = true;
    try {
      const data = await api("api/migration", {});
      $("migration-summary").textContent =
        `${data.summary.preserved} preserved, ${data.summary.review} need review, ${data.summary.new} new. ${data.guidance}`;
      const body = $("migration-table").querySelector("tbody");
      body.replaceChildren();
      for (const row of data.entities) {
        const tr = node("tr");
        for (const text of [
          row.inverter + " / " + row.measurement,
          row.existing_entity || "No exact match",
          row.status + (row.has_statistics ? "; statistics present" : ""),
          row.used_in_energy
            ? "Already selected"
            : row.recommended_solar_total
              ? "Suggested solar total"
              : row.energy_eligible
                ? "Eligible; avoid counting twice"
                : "Not an energy total",
          row.issues.join(". ") || "No conflict found",
        ]) {
          tr.append(node("td", text));
        }
        body.append(tr);
      }
      $("migration-table").hidden = false;
    } finally {
      $("migration").disabled = false;
    }
  }),
);
$("refresh").addEventListener("click", () => action(refresh));
action(refresh);
