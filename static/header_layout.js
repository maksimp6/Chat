(function () {
    "use strict";

    function installHeaderLayout() {
        if (document.getElementById("header-layout-overrides")) return;

        const style = document.createElement("style");
        style.id = "header-layout-overrides";
        style.textContent = `
            .alice-pro-app #header { gap: 8px; }
            .alice-pro-app #header > .header-actions {
                display: flex;
                flex: 1 1 auto;
                min-width: 0;
                flex-wrap: wrap;
                align-items: flex-start;
                align-content: flex-start;
                justify-content: flex-start;
                column-gap: 12px;
                row-gap: 8px;
            }
            .alice-pro-app #header > .header-actions > .header-btn {
                flex: 0 0 38px;
                width: 38px;
                height: 38px;
            }
            .alice-pro-app #header > .header-actions > .header-btn.header-row-start {
                margin-left: auto;
            }
            @media (max-width: 768px) {
                .alice-pro-app #header { gap: 6px; }
                .alice-pro-app #header > .header-actions {
                    column-gap: 8px;
                    row-gap: 6px;
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
                button.style.marginLeft = "auto";
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
