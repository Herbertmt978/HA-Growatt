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
let refreshing = false;
let displayedDevices = "";
async function refresh(renderForms = true) {
  if (refreshing) return;
  refreshing = true;
  try {
  const data = await api("api/status");
  renderInstallation(data);
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
  $("packet-summary").textContent = `${data.observations.measurements} measurements decoded; ${data.observations.failed_measurements} failed measurements; ${data.observations.incomplete_fields} incomplete fields; ${data.observations.announcement_warnings} announcement warnings.`;
  $("capture-status").textContent = data.capture_active ? "Recording packet summaries (up to ten minutes)." : "Capture is stopped.";
  $("private-status").textContent = data.private_capture_active ? "Recording private packets." : "Private capture is stopped.";
  $("packet-health").replaceChildren(...(data.packet_health || []).map((row) => node("li", `${row.source}: ${row.state}. Format changes: ${row.changes}; incomplete or failed measurements: ${row.failures}.`)));
  $("version").textContent = `HA Growatt ${data.version}`;
  $("dataloggers").replaceChildren();
  for (const logger of data.dataloggers || []) {
    const card = node("div");
    card.className = "device";
    card.append(node("h3", logger.model ? `${logger.model} · ${logger.identity}` : logger.identity));
    card.append(node("p", `${logger.connection} · Firmware: ${logger.firmware || "unconfirmed"}`));
    card.append(node("p", `Last contact: ${logger.last_contact ? new Date(logger.last_contact).toLocaleString() : "No contact this service run"}. Observed upload interval: ${logger.upload_interval == null ? "waiting for two readings" : logger.upload_interval + " seconds"}. Reconnects: ${logger.reconnects}.`));
    if (logger.history.length) {
      const history = node("ul");
      for (const stamp of logger.history) history.append(node("li", new Date(stamp).toLocaleString()));
      card.append(history);
    }
    $("dataloggers").append(card);
  }
  const controlDetails = (device) => (device.capabilities?.settings || []).map((setting) => node("li", `${setting.label}: ${setting.reason}`));
  const identities = JSON.stringify(data.devices.map((device) => device.identity));
  if (!renderForms && identities !== displayedDevices &&
      !$("devices").querySelector('[data-dirty="true"]')) renderForms = true;
  const healthText = (device) => device.clock ? ` Operating state: ${device.operating_state}. Main fault: ${device.fault_description}. Clock: ${device.clock_status}; reference: ${device.clock.reference}${device.clock.offset_seconds == null ? "" : "; offset: " + device.clock.offset_seconds + " seconds"}. ${device.write_conflict}.${device.firmware_changed ? " Recorded firmware changed this service run; settings require fresh readback." : ""}` : "";
  const description = (device) => `${device.restored ? "Saved readings; waiting for fresh data" : device.recent ? "Recent readings" : "No recent readings"} · ${device.profile} · ${device.connection}. ${device.capabilities?.explanation || ""}${device.datalogger ? " Logger: " + device.datalogger + "." : ""}` + healthText(device);
  if (!renderForms) {
    for (const card of $("devices").children) {
      const device = data.devices.find((item) => item.identity === card.dataset.identity);
      if (device) {
        card.querySelector("p").textContent = description(device);
        card.querySelector(".control-details").replaceChildren(...controlDetails(device));
      }
    }
    return;
  }
  displayedDevices = identities;
  invalidatePeriod();
  $("devices").replaceChildren();
  const previous = $("schedule-device").value;
  $("schedule-device").replaceChildren();
  for (const device of data.devices) {
    const section = node("div");
    section.className = "device";
    section.dataset.identity = device.identity;
    section.append(
      node("h3", device.identity),
      node(
        "p",
        description(device),
      ),
    );
    const details = node("ul");
    details.className = "control-details";
    details.replaceChildren(...controlDetails(device));
    section.append(details);
    const form = node("form"),
      fields = node("div");
    fields.className = "fields";
    form.addEventListener("input", () => { form.dataset.dirty = "true"; });
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
    const hardware = {};
    for (const [key, title] of [["model", "Exact inverter model"], ["firmware", "Firmware version"]]) {
      const label = node("label", title), input = node("input");
      input.type = "text";
      input.maxLength = 80;
      input.value = device[key] || "";
      input.placeholder = "Unknown — leave blank";
      label.append(input);
      fields.append(label);
      hardware[key] = input;
    }
    const save = node("button", "Save inverter details");
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
            model: hardware.model.value.trim(),
            firmware: hardware.firmware.value.trim(),
          });
          $("message").textContent = response.message;
          await refresh();
        } finally {
          save.disabled = false;
        }
      });
    });
    section.append(form);
    section.append(registerTools(device, hardware, form));
    const readFirmware = node("button", "Read firmware from inverter");
    readFirmware.addEventListener("click", () => action(async () => {
      readFirmware.disabled = true;
      try {
        const result = await api("api/hardware/read", { serial: device.identity });
        hardware.firmware.value = result.firmware;
        $("message").textContent = result.message;
      } finally { readFirmware.disabled = false; }
    }));
    section.append(readFirmware);
    $("devices").append(section);
    if (
      device.capabilities?.schedules?.length &&
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
  } catch (error) {
    installationUnavailable();
    throw error;
  } finally {
    refreshing = false;
  }
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
$("refresh").addEventListener("click", () => action(() => refresh()));
for (const operation of ["start", "stop"]) {
  $("capture-" + operation).addEventListener("click", () => action(async () => {
    const response = await api("api/capture/" + operation, {});
    $("message").textContent = response.message;
    await refresh(false);
  }));
}
for (const operation of ["start", "stop", "clear"]) {
  $("private-" + operation).addEventListener("click", () => action(async () => {
    if (operation === "start" && !$("private-consent").checked) throw Error("Please acknowledge that the capture contains private data.");
    const response = await api("api/private-capture/" + operation, {acknowledge_private_data: $("private-consent").checked});
    $("message").textContent = response.message;
    await refresh(false);
  }));
}
setInterval(() => {
  if (!document.hidden) action(() => refresh(false));
}, 10000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) action(() => refresh(false));
});
action(refresh);
