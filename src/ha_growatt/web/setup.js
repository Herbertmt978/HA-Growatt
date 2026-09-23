"use strict";
// All setup checks are observations. Configuration stays in the existing forms.
const setupElement = (id) => document.getElementById(id);
const setupNode = (tag, text) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  return element;
};
const setupSteps = ["Broker", "Datalogger", "First readings", "Profiles", "History", "Finish"];
let setupStep = 0;
let setupStatus = null;
let setupPort = null;
let setupCatalogue = null;
let setupDevices = null;
const setupReviewed = { profiles: false, history: false, entities: false };

function setupLink(label, href) {
  const link = setupNode("a", label);
  link.href = href;
  return link;
}
function setupHomeAssistantLink(label, href) {
  const link = setupLink(label, href);
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}
function setupParagraph(text) {
  setupElement("setup-instructions").append(setupNode("p", text));
}
function setupReview(key, text) {
  const label = setupNode("label");
  label.className = "setup-review";
  const input = setupNode("input");
  input.type = "checkbox";
  input.checked = setupReviewed[key];
  input.addEventListener("change", () => {
    setupReviewed[key] = input.checked;
    updateSetupChecks();
  });
  label.append(input, setupNode("span", text));
  setupElement("setup-instructions").append(label);
}
function showSetupStep(focus = false) {
  setupElement("setup-step-title").textContent = `Step ${setupStep + 1} of ${setupSteps.length}: ${setupSteps[setupStep]}`;
  for (const [index, button] of [...setupElement("setup-nav").children].entries()) {
    if (index === setupStep) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  }
  setupElement("setup-back").disabled = setupStep === 0;
  setupElement("setup-next").disabled = setupStep === setupSteps.length - 1;
  const instructions = setupElement("setup-instructions");
  instructions.replaceChildren();
  const migrating = setupElement("setup-kind").value === "migration";
  if (setupStep === 0) {
    setupParagraph("Install and start Mosquitto broker before starting this app. Add the MQTT integration under Settings → Devices & services and connect it to the same broker. For automatic broker configuration, leave mqtt_auto enabled and the app's MQTT username and password empty. Existing explicit credentials take priority.");
    setupParagraph("For an external broker, enter its address and credentials in the app configuration. A connected app cannot prove that Home Assistant uses the same broker: check the entities in the final step.");
    setupParagraph("If the app cannot start, open its Log tab first. Automatic configuration needs a working Supervisor MQTT service. This web guide is available only while the app is running.");
  } else if (setupStep === 1) {
    setupParagraph("Give Home Assistant a stable LAN address. In the datalogger's own settings, set the upload server to that address, using the published TCP port below. Do not use the ingress URL or the app's internal container address. Keep the existing cloud forwarding settings unless you deliberately want local-only operation.");
    setupParagraph(setupPort === null
      ? "The published TCP port is not available here. Check the app's Network settings and publish its datalogger port before changing the logger. Do not assume the container port is the host port."
      : `Published datalogger TCP port: ${setupPort}. The server address must be Home Assistant's LAN address.`);
    setupParagraph("ShineLAN/LAN-X and ShineWiFi variants have different configuration screens. Use the instructions for your exact datalogger and firmware to change its server. If server changes are unavailable, do not flash firmware or alter your network merely to complete this guide.");
    if (migrating) setupParagraph("Save the old service's configuration and record its broker, topics and discovery settings first. Stop it before HA Growatt takes over the same listening port. Keep a recovery copy until daylight readings and history are checked.");
    setupParagraph("Some session-key encrypted Shine traffic cannot be decoded. A connection or incoming packet alone does not establish compatibility.");
    instructions.append(setupLink("Check datalogger limitations", "#hardware-title"));
  } else if (setupStep === 2) {
    setupParagraph("Enter the number of inverters you expect above. Wait for a fresh reading from each one, then compare power and daily energy with the inverter display or your existing readings. Saved readings after a restart do not pass this check. Solar-only inverters may stay silent overnight; check again in daylight.");
    setupParagraph("Packets without decoded readings usually need investigation of the protocol, layout or encryption. Open Connection checks and download redacted diagnostics if the feed remains silent or cannot be decoded.");
    instructions.append(setupLink("View connection checks", "#connection-title"));
  } else if (setupStep === 3) {
    setupParagraph("Devices appear below after they are discovered. Keep the reading profile on Automatic while readings are correct. Match any override to the exact model, separately for each inverter. Record the model from its label; read firmware when supported or leave it unknown.");
    setupParagraph("Telemetry support does not qualify battery controls. Leave experimental controls disabled unless you are deliberately testing matching hardware. Another project's Modbus result does not prove that the same setting works through your datalogger.");
    instructions.append(setupLink("Review inverter profiles", "#devices-title"));
    setupReview("profiles", "I have checked each inverter's readings and profile.");
  } else if (setupStep === 4) {
    setupParagraph(migrating
      ? "Use Check existing entities below before retiring the old service. Compare unique IDs, entity names, Recorder history and Energy sensors. Keep the same broker, topics and discovery settings. The preview does not move or delete history."
      : "Open History and Energy below to inspect sensor metadata. In Home Assistant, choose the production energy sensors you actually need. Power in W or kW is not an accumulated energy total in kWh.");
    setupParagraph("Check whether another solar sensor already includes these inverters before adding their energy totals. Selecting both the combined total and its components counts the same production twice.");
    instructions.append(setupLink("Open the history and Energy checks", "#migration-title"));
    setupReview("history", migrating ? "I have reviewed existing entities, history and Energy mappings." : "I have reviewed my Energy Dashboard coverage.");
  } else {
    setupParagraph("In Settings → Devices & services → MQTT, check that every expected inverter has fresh measurement entities. This confirms the Home Assistant side of discovery. The app's checks alone cannot confirm the contents of your dashboard.");
    setupReview("entities", "I have checked the inverter entities in Home Assistant.");
    setupParagraph("The optional companion adds daylight-aware Repairs and history tools. HACS installs the companion; this app still receives datalogger traffic and publishes readings. Download the companion from HACS, restart Home Assistant, then add HA Growatt under Devices & services.");
    const companionLinks = setupNode("p");
    companionLinks.append(
      setupHomeAssistantLink("Open HA Growatt in HACS", "https://my.home-assistant.io/redirect/hacs_repository/?owner=Herbertmt978&repository=HA-Growatt&category=integration"),
      setupNode("span", " · "),
      setupHomeAssistantLink("Add the companion integration", "https://my.home-assistant.io/redirect/config_flow_start/?domain=ha_growatt"),
    );
    instructions.append(companionLinks);
    setupParagraph("The companion checks for a fresh app status message on Home Assistant's MQTT broker. If it cannot see one, confirm that Home Assistant and this app use the same broker. You can still finish setup while the app is restarting or offline.");
    instructions.append(setupLink("Read the companion instructions", "https://github.com/Herbertmt978/HA-Growatt/blob/main/docs/home-assistant-features.md"));
    setupParagraph("These checks apply to the current connection. They are not a hardware certification or a battery-control test. Review acknowledgements reset when you reload this page.");
  }
  updateSetupChecks();
  if (focus) setupElement("setup-step-title").focus();
}
function updateSetupChecks() {
  const checks = setupStatus?.installation;
  const countInput = setupElement("setup-count");
  const countValid = countInput.validity.valid && countInput.value !== "";
  const count = countValid ? Number(countInput.value) : null;
  const fresh = Boolean(checks && countValid && checks.inverters_seen === count && checks.all_seen_inverters_fresh);
  const broker = Boolean(checks?.broker && checks.discovery);
  const rows = [
    [broker, checks?.broker ? (checks.discovery ? "Broker connected; MQTT discovery enabled." : "Enable Home Assistant discovery in the app options.") : "Waiting for the MQTT broker connection."],
    [Boolean(checks?.listener && checks?.packets), checks?.packets ? "Datalogger packets received." : "Waiting for datalogger packets."],
    [fresh, checks && !checks.device_status ? "Enable ha_features in the app options to check device readings." : !countValid ? "Enter an inverter count from 1 to 100." : `${checks?.fresh_inverters ?? 0} fresh inverter feeds; ${checks?.inverters_seen ?? 0} discovered; ${count} expected.`],
    [setupReviewed.profiles, "Review the readings and profiles for each inverter."],
    [setupReviewed.history, "Review history and Energy coverage."],
    [setupReviewed.entities, "Check measurement entities in Home Assistant."],
  ];
  const visible = setupStep === 5 ? rows : [rows[setupStep]];
  setupElement("setup-checks").replaceChildren(...visible.map(([passed, text]) => setupNode("li", `${passed ? "Checked" : "To check"}: ${text}`)));
  const complete = Boolean(setupStatus && rows.every(([passed]) => passed));
  setupElement("setup-summary").textContent = !setupStatus
    ? "Connection checks unavailable. Setup cannot be confirmed until the app responds."
    : complete ? "Current connection checks and your review are complete. Battery controls remain separately qualified."
      : "Setup still has checks to complete. You can visit every step while waiting for readings.";
}
function renderInstallation(status) {
  const devices = JSON.stringify(status.devices.map(({identity, family, controls, profile, model}) => ({identity, family, controls, profile, model})));
  if (setupDevices !== null && devices !== setupDevices) {
    setupReviewed.profiles = false;
    setupReviewed.entities = false;
    setupReviewed.history = false;
    // Reflect invalidated acknowledgements without rebuilding the profile forms.
    for (const input of setupElement("setup-instructions").querySelectorAll('input[type="checkbox"]')) input.checked = false;
  }
  setupDevices = devices;
  setupStatus = status;
  updateSetupChecks();
}
function installationUnavailable() {
  setupStatus = null;
  updateSetupChecks();
}
function renderHardware() {
  if (!setupCatalogue) return;
  const query = setupElement("hardware-search").value.trim().toLocaleLowerCase();
  const level = setupElement("hardware-level").value;
  const entries = setupCatalogue.entries.filter((entry) =>
    (!level || entry.level === level) && JSON.stringify(entry).toLocaleLowerCase().includes(query));
  setupElement("hardware-count").textContent = entries.length ? `${entries.length} evidence records. Open a record for its limits and sources.` : "No matching evidence. An unlisted model is unverified, not necessarily unsupported.";
  setupElement("hardware-results").replaceChildren(...entries.map((entry) => {
    const details = setupNode("details");
    details.className = "hardware-result";
    details.append(setupNode("summary", `${entry.model} — ${entry.level}`));
    const list = setupNode("dl");
    for (const [key, title] of Object.entries({project: "Tested software or source project", firmware: "Firmware", datalogger: "Datalogger", connection: "Connection", telemetry: "Readings", controls: "Controls", profile: "Profile guidance", limits: "Limits"})) {
      list.append(setupNode("dt", title), setupNode("dd", entry[key]));
    }
    details.append(list);
    const sources = setupNode("ul");
    for (const source of entry.sources) {
      const item = setupNode("li");
      const link = setupLink(source.label, source.url);
      link.target = "_blank";
      link.rel = "noreferrer";
      item.append(link);
      sources.append(item);
    }
    details.append(sources);
    return details;
  }));
}
for (const [index, title] of setupSteps.entries()) {
  const button = setupNode("button", `${index + 1}. ${title}`);
  button.type = "button";
  button.addEventListener("click", () => { setupStep = index; showSetupStep(true); });
  setupElement("setup-nav").append(button);
}
setupElement("setup-next").addEventListener("click", () => { setupStep++; showSetupStep(true); });
setupElement("setup-back").addEventListener("click", () => { setupStep--; showSetupStep(true); });
setupElement("setup-kind").addEventListener("change", () => { setupReviewed.history = false; showSetupStep(); });
setupElement("setup-count").addEventListener("input", () => {
  setupReviewed.profiles = false;
  setupReviewed.entities = false;
  showSetupStep();
});
setupElement("hardware-search").addEventListener("input", renderHardware);
setupElement("hardware-level").addEventListener("change", renderHardware);
showSetupStep();
// Defer requests until the shared API helper in app.js has been initialised.
document.addEventListener("DOMContentLoaded", async () => {
  try {
    const result = await api("api/installation");
    setupPort = result.host_port;
    if (setupStep === 1) showSetupStep();
  } catch {
    setupPort = null;
  }
  try {
    setupCatalogue = await api("api/compatibility");
    setupElement("hardware-notice").textContent = `${setupCatalogue.notice} Evidence checked ${setupCatalogue.checked}.`;
    for (const level of new Set(setupCatalogue.entries.map((entry) => entry.level))) {
      const option = setupNode("option", level);
      option.value = level;
      setupElement("hardware-level").append(option);
    }
    renderHardware();
  } catch {
    setupElement("hardware-notice").textContent = "The evidence catalogue could not be loaded. Use the published matrix below or reload this page.";
  }
});
