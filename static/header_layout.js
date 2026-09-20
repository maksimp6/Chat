(function () {
    "use strict";

    function installHeaderLayout() {
        if (document.getElementById("header-layout-overrides")) return;

        const style = document.createElement("style");
        style.id = "header-layout-overrides";
        style.textContent = `
            .alice-pro-app #header { gap: 8px !important; }
            .alice-pro-app #header > .header-actions {
                display: flex !important;
                flex: 1 1 auto !important;
                min-width: 0 !important;
                flex-wrap: wrap !important;
                align-items: flex-start !important;
                align-content: flex-start !important;
                justify-content: flex-start !important;
                column-gap: 12px !important;
                row-gap: 8px !important;
            }
            .alice-pro-app #header > .header-actions > .header-btn {
                flex: 0 0 38px !important;
                width: 38px !important;
                height: 38px !important;
                margin-right: 0;
            }
            .alice-pro-app #header > .header-actions > .header-btn.header-row-start {
                margin-left: auto !important;
            }
            @media (max-width: 768px) {
                .alice-pro-app #header { gap: 6px !important; }
                .alice-pro-app #header > .header-actions {
                    column-gap: 8px !important;
                    row-gap: 6px !important;
                }
            }
        `;
        document.head.appendChild(style);
    }

    function alignWrappedRows() {
        const header = document.getElementById("header");
        const actions = header && header.querySelector(".header-actions");
        if (!actions) return;

        const buttons = Array.from(actions.querySelectorAll(".header-btn"));
        buttons.forEach((button) => {
            button.classList.remove("header-row-start");
            button.style.removeProperty("margin-left");
        });

        let previousTop = null;
        buttons.forEach((button, index) => {
            const top = button.offsetTop;
            if (index > 0 && previousTop !== null && top > previousTop) {
                button.classList.add("header-row-start");
            }
            previousTop = top;
        });
    }

    function scheduleAlignment() {
        window.requestAnimationFrame(alignWrappedRows);
    }

    function init() {
        installHeaderLayout();
        scheduleAlignment();

        const header = document.getElementById("header");
        const actions = header && header.querySelector(".header-actions");
        if (!actions) return;

        window.addEventListener("resize", scheduleAlignment);
        if (typeof ResizeObserver === "function") {
            new ResizeObserver(scheduleAlignment).observe(actions);
        }
        if (typeof MutationObserver === "function") {
            new MutationObserver(scheduleAlignment).observe(actions, { childList: true });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init, { once: true });
    } else {
        init();
    }
})();
