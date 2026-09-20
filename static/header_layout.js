(function () {
    "use strict";

    function alignWrappedRows() {
        const header = document.getElementById("header");
        const actions = header && header.querySelector(".header-actions");
        if (!actions) return;

        const buttons = Array.from(actions.querySelectorAll(".header-btn"));
        buttons.forEach((button) => button.classList.remove("header-row-start"));

        let previousTop = null;

        buttons.forEach((button, index) => {
            const top = button.offsetTop;

            if (index > 0 && top > previousTop) {
                button.classList.add("header-row-start");
            }

            previousTop = top;
        });
    }

    function scheduleAlignment() {
        window.requestAnimationFrame(alignWrappedRows);
    }

    document.addEventListener("DOMContentLoaded", scheduleAlignment);
    window.addEventListener("resize", scheduleAlignment);

    if (typeof ResizeObserver === "function") {
        const header = document.getElementById("header");
        const actions = header && header.querySelector(".header-actions");

        if (actions) {
            new ResizeObserver(scheduleAlignment).observe(actions);
        }
    }
})();
