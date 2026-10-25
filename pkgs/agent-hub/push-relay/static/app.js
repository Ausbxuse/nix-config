"use strict";
const $ = id => document.getElementById(id);
const fragment = location.hash.slice(1);
if (/^[A-Za-z0-9_-]{40,60}$/.test(fragment)) sessionStorage.setItem("hub-pair", fragment);
history.replaceState(null, "", location.pathname);
$("ticket").value = sessionStorage.getItem("hub-pair") || "";
let registration, publicKey;
let device;
try { device = JSON.parse(localStorage.getItem("hub-push-device") || "null"); } catch { device = null; }
const status = text => { $("status").textContent = text; };
async function api(path, body) {
  const response = await fetch("./" + path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(30000) });
  const result = await response.json(); if (!response.ok) throw new Error(result.error); return result;
}
function showConnected() {
  $("connected").hidden = false; $("enable").hidden = true; $("pairing").hidden = true;
  $("back").href = device.hub_url + "/#updates=1";
  status("Notifications are enabled on this browser. They can arrive with the hub closed.");
}
$("enable").onclick = async () => {
  try {
    const ticket = $("ticket").value.trim();
    if (!/^[A-Za-z0-9_-]{40,60}$/.test(ticket)) throw new Error("Open Enable push in your private hub to pair this browser.");
    // Request permission directly from the click, before asynchronous work.
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("Notifications were not allowed. You can change this in browser site settings.");
    $("enable").disabled = true; status("Connecting notifications…");
    const key = Uint8Array.from(atob(publicKey.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - publicKey.length % 4) % 4)), c => c.charCodeAt(0));
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    device = await api("subscribe", { ticket, subscription: subscription.toJSON() });
    localStorage.setItem("hub-push-device", JSON.stringify(device)); sessionStorage.removeItem("hub-pair");
    showConnected();
  } catch (error) { status(error.message); }
  finally { $("enable").disabled = false; }
};
$("test").onclick = async () => {
  $("test").disabled = true;
  try { await api("test", device); status("Test sent to this browser. Check your notifications."); }
  catch (error) { status(error.message); }
  finally { $("test").disabled = false; }
};
$("disable").onclick = async () => {
  try {
    await api("unsubscribe", device);
    await (await registration.pushManager.getSubscription())?.unsubscribe();
    localStorage.removeItem("hub-push-device"); device = null;
    $("connected").hidden = true; $("enable").hidden = false; $("pairing").hidden = false;
    status("Notifications stopped on this device. Other devices are still connected.");
  } catch (error) { status(error.message); }
};
(async () => {
  try {
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      $("pairing").hidden = false;
      throw new Error("This browser does not support push here. On iPhone or iPad, add this page to your Home Screen and open it there. On desktop or Android, use a browser with Web Push support.");
    }
    registration = await navigator.serviceWorker.register("./sw.js", { scope: "./" });
    await navigator.serviceWorker.ready;
    publicKey = (await api("info")).public_key;
    if (device && !$("ticket").value && Notification.permission === "granted" && await registration.pushManager.getSubscription()) return showConnected();
    $("pairing").hidden = !/iPhone|iPad/.test(navigator.userAgent) && Boolean($("ticket").value);
    $("enable").disabled = false;
    status($("ticket").value ? "Ready to enable notifications on this device." : "Open Updates → Enable push in your private hub to connect this browser.");
  } catch (error) { status(error.message); }
})();
