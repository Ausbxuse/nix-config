"use strict";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("push", event => {
  let value = {};
  try { value = event.data.json(); } catch {}
  event.waitUntil(self.registration.showNotification(value.title || "Agent Hub", {
    body: value.body || "There is an agent update in your hub.", tag: value.id || "agent-hub-update",
    icon: "./icon.svg", data: { url: value.url }, renotify: false,
  }));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = event.notification.data?.url;
  if (!url || !/^https?:\/\//.test(url)) return;
  event.waitUntil(self.clients.openWindow(url));
});
