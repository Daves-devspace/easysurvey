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
    apiKey: "AIzaSyChEMefjB-MyXvYPMiGHLVAswbJ9c3dbyY",
    authDomain: "smartsurveyor-79625.firebaseapp.com",
    projectId: "smartsurveyor-79625",
    storageBucket: "smartsurveyor-79625.firebasestorage.app",
    messagingSenderId: "696735882272",
    appId: "1:696735882272:web:223d2cc7603e724efe7045"
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

