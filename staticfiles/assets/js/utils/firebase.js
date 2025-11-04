import { initializeApp } from "https://www.gstatic.com/firebasejs/10.0.0/firebase-app.js";
import { getMessaging, getToken, onMessage } from "https://www.gstatic.com/firebasejs/10.0.0/firebase-messaging.js";

async function waitForSWActive(registration) {
  if (registration.active) return registration.active;

  return new Promise((resolve, reject) => {
    const serviceWorker = registration.installing || registration.waiting;
    if (!serviceWorker) return reject("No installing or waiting SW found");

    serviceWorker.addEventListener("statechange", () => {
      if (serviceWorker.state === "activated") resolve(serviceWorker);
      if (serviceWorker.state === "redundant") reject("SW became redundant");
    });
  });
}

export async function initFCM(firebaseConfig, vapidKey) {
  const app = initializeApp(firebaseConfig);
  const messaging = getMessaging(app);

  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      console.warn("Notifications blocked by user.");
      return;
    }

    const registration = await navigator.serviceWorker.register('/firebase-messaging-sw.js');
    console.log("✅ Service Worker registered:", registration);

    // Wait for SW to be active before getting token
    await waitForSWActive(registration);
    console.log("✅ Service Worker active");

    const token = await getToken(messaging, { serviceWorkerRegistration: registration, vapidKey });

    if (token) {
      console.log("✅ FCM Token:", token);
      await fetch("/notifications/api/save-fcm-token/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCookie("csrftoken"),
        },
        body: JSON.stringify({ token }),
      });
    }

    onMessage(messaging, (payload) => {
      const { title, body } = payload.notification;
      new Notification(title, { body });
    });

  } catch (err) {
    console.error("⚠️ FCM initialization error:", err);
  }
}

function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(";").shift();
}
