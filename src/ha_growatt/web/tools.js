"use strict";
function registerTools(device, hardware, profileForm) {
  const panel = node("details");
  panel.append(node("summary", "Identify hardware and inspect registers"));
  panel.append(node("p", "These tools only read holding registers. Keep the inverter connected. Identification may take up to 45 seconds; unsupported fields stay unconfirmed. Reading profile and controls remain unchanged."));
  const identify = node("button", "Identify hardware"), report = node("pre");
  const status = node("p");
  status.setAttribute("role", "status");
  const apply = node("button", "Copy reported details into the form");
  apply.disabled = true;
  let identityReport = null, reading = null, previous = null, busy = false;
  const form = node("form"), fields = node("div");
  fields.className = "fields";
  const inputs = {};
  for (const [key, title, value, max] of [["start", "First holding register", 9, 65535], ["count", "Number of registers (1–32)", 6, 32]]) {
    const label = node("label", title), input = node("input");
    input.type = "number"; input.step = "1"; input.min = key === "count" ? "1" : "0";
    input.max = String(max); input.value = String(value); input.required = true;
    label.append(input); fields.append(label); inputs[key] = input;
  }
  const read = node("button", "Read registers"), compare = node("button", "Read again and compare");
  compare.type = "button"; compare.disabled = true;
  const download = node("button", "Download private register report");
  download.disabled = true;
  const output = node("pre");
  const controls = [identify, read, compare, apply, download];
  async function run(work) {
    if (busy) return;
    busy = true;
    controls.forEach((button) => { button.disabled = true; });
    inputs.start.disabled = inputs.count.disabled = true;
    // Keep auto-refresh from replacing this panel while a read is outstanding.
    profileForm.dataset.dirty = "true";
    status.textContent = "Reading from the inverter…";
    try { await work(); } catch (error) { status.textContent = error.message; }
    finally {
      busy = false;
      identify.disabled = read.disabled = false;
      inputs.start.disabled = inputs.count.disabled = false;
      compare.disabled = !previous;
      apply.disabled = !identityReport || !(identityReport.model || identityReport.firmware);
      download.disabled = !reading;
    }
  }
  identify.addEventListener("click", () => run(async () => {
    identityReport = null; report.textContent = "";
    identityReport = await api("api/register-diagnostics", {identity: device.identity, operation: "identify"});
    report.textContent = `Reported model: ${identityReport.model || "Unconfirmed"}\nFirmware: ${identityReport.firmware || "Unconfirmed"}\nFamily hint: ${identityReport.family_hint || "Unconfirmed"}\nDTC: ${identityReport.dtc ?? "Unavailable"}\nVPP version code: ${identityReport.vpp_version ?? "Unavailable"}\nCurrent reading profile: ${identityReport.reading_profile}\nConfidence: ${identityReport.confidence}\n\n` + identityReport.evidence.map((item) => `${item.field}: ${item.status} (holding ${item.start}–${item.start + item.count - 1}; ${item.source})`).join("\n");
    status.textContent = "Compare the reported model with the inverter label. Copy details only if they match, then use Save inverter details.";
  }));
  apply.addEventListener("click", () => {
    for (const key of ["model", "firmware"]) if (identityReport?.[key]) hardware[key].value = identityReport[key];
    profileForm.dataset.dirty = "true";
    status.textContent = "Details copied into the form. Review them and select Save inverter details to keep them.";
  });
  function invalidate() {
    previous = null; reading = null; output.textContent = "";
    compare.disabled = download.disabled = true;
  }
  form.addEventListener("input", invalidate);
  async function readBlock(comparing) {
    const prior = comparing ? previous : null;
    previous = null;
    const result = await api("api/register-diagnostics", {
      identity: device.identity, operation: "read", start: Number(inputs.start.value), count: Number(inputs.count.value),
      ...(prior ? {previous: prior} : {}),
    });
    reading = result; previous = result.snapshot;
    output.textContent = result.words.map((value, index) => `${result.start + index}: ${value} (0x${value.toString(16).padStart(4, "0")})`).join("\n");
    if (result.changes !== null) output.textContent += "\n\n" + (result.changes.length ? result.changes.map((change) => `${change.address}: ${change.before} → ${change.after}`).join("\n") : "No register values changed.");
    status.textContent = "Read complete. Wait a few seconds before comparing. The comparison expires after ten minutes or a connection or profile change.";
  }
  form.addEventListener("submit", (event) => { event.preventDefault(); run(() => readBlock(false)); });
  compare.addEventListener("click", () => { if (form.reportValidity()) run(() => readBlock(true)); });
  download.addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(reading, null, 2)], {type: "application/json"}));
    const link = node("a"); link.href = url; link.download = "ha-growatt-private-registers.json";
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  form.append(fields, read, compare);
  panel.append(identify, report, apply, node("h4", "Register inspector"), node("p", "For troubleshooting with the register table for your exact model. Addresses are decimal and start at zero. Values are raw unsigned 16-bit words; no units or meanings are assumed. The report may contain serial numbers or other private values. Do not post it publicly."), form, output, download, status);
  return panel;
}
