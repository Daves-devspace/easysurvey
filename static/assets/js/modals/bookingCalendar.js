let calendar; // so it’s accessible globally

document.addEventListener("DOMContentLoaded", function () {
  const calendarEl = document.getElementById("calendar");
  const API_URL = "/api/calendar/bookings/";

  function buildCalendar() {
    return new FullCalendar.Calendar(calendarEl, {
      initialView: window.innerWidth < 576 ? "timeGridDay" : "dayGridMonth",
      height: "auto",
      headerToolbar:
        window.innerWidth < 576
          ? {
              left: "prev,next",
              center: "title",
              right: "customViews",
            }
          : {
              left: "prev,next today",
              center: "title",
              right: "dayGridMonth,timeGridWeek,timeGridDay",
            },
      customButtons: {
        customViews: {
          text: "Views",
          click: function () {
            document.getElementById("viewSelector").classList.toggle("d-none");
          },
        },
      },
      eventSources: [
        {
          url: API_URL,
          extraParams: () => ({
            handled: document.getElementById("toggle-handled").checked ? 1 : 0,
            summary: 1,
          }),
          display: "background",
          filter: (info) => info.view.type === "dayGridMonth",
          eventDidMount: function (info) {
            const details = info.event.extendedProps.details || [];
            if (details.length > 0 && window.innerWidth > 576) {
              tippy(info.el, {
                content: details.join("<br>"),
                allowHTML: true,
                theme: "light",
                placement: "top",
              });
            }
          },
        },
        {
          url: API_URL,
          extraParams: () => ({
            handled: document.getElementById("toggle-handled").checked ? 1 : 0,
            summary: 0,
          }),
          display: "auto",
          filter: (info) => info.view.type !== "dayGridMonth",
        },
      ],
      eventClick: function (info) {
        const props = info.event.extendedProps;
        const modalHtml = `
            <strong>Client:</strong> 
            <a href="/clients/details/${props.client_id}/">${
          props.client
        }</a><br>
            <strong>Service:</strong> ${props.service || "N/A"}<br>
            <strong>Time:</strong> ${
              props.time || info.event.start.toLocaleString()
            }<br>
            <strong>Message:</strong> ${props.dispatchMessage || "N/A"}<br>
            <strong>Status:</strong> ${props.handled ? "Handled" : "Pending"}
          `;
        document.getElementById("modalBody").innerHTML = modalHtml;
        new bootstrap.Modal(document.getElementById("bookingModal")).show();
      },
    });
  }

  calendar = buildCalendar();
  calendar.render();

  document
    .getElementById("toggle-handled")
    .addEventListener("change", () => calendar.refetchEvents());

  window.addEventListener("resize", () => {
    const currentView = calendar.view.type;
    if (window.innerWidth < 576 && currentView !== "timeGridDay") {
      calendar.changeView("timeGridDay");
    } else if (window.innerWidth >= 576 && currentView === "timeGridDay") {
      calendar.changeView("dayGridMonth");
    }
  });
});
