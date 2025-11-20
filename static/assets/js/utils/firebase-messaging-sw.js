// static/firebase-messaging-sw.js

/**
 * firebase-messaging-sw.template.js
 * ---------------------------------
 * Template for firebase-messaging-sw.js.
 * Rendered at container start (entrypoint.sh) with envsubst.
 * 
 * DO NOT hardcode secrets here.
 */
importScripts("https://www.gstatic.com/firebasejs/10.0.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.0.0/firebase-messaging-compat.js");

try {
  firebase.initializeApp({
    apiKey: "$FIREBASE_API_KEY",
    authDomain: "$FIREBASE_AUTH_DOMAIN",
    projectId: "$FIREBASE_PROJECT_ID",
    storageBucket: "$FIREBASE_STORAGE_BUCKET",
    messagingSenderId: "$FIREBASE_MESSAGING_SENDER_ID",
    appId: "$FIREBASE_APP_ID"
  });
  console.log("✅ Firebase initialized in Service Worker");
} catch (e) {
  console.error("⚠️ Firebase initialization failed:", e);
}

const messaging = firebase.messaging();

messaging.onBackgroundMessage(function (payload) {
  console.log("📩 Background message received:", payload);
  const title = payload.notification?.title || "Notification";
  const options = {
    body: payload.notification?.body || payload.data?.message || "",
    icon: "/static/assets/img/notification-icon.png",
  };
  self.registration.showNotification(title, options);
});

