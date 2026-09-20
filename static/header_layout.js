(function () {
    "use strict";

    function installHeaderLayout() {
        if (document.getElementById("header-layout-overrides")) return;

        const style = document.createElement("style");
        style.id = "header-layout-overrides";
        style.textContent = `
            .alice-pro-app #header { gap: 12px !important; }
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
                margin-right: 0 !important;
            }
            .alice-pro-app #header > .header-actions > .header-btn.header-row-start {
                margin-left: 0 !important;
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
        if (buttons.length < 2) return;

        const firstRect = buttons[0].getBoundingClientRect();
        const secondRect = buttons[1].getBoundingClientRect();
        const buttonWidth = firstRect.width;
        const columnGap = Math.max(0, secondRect.left - firstRect.right);
        const pitch = buttonWidth + columnGap;
        const columns = Math.max(1, Math.floor((actions.clientWidth + columnGap) / pitch));

        let rowStart = 0;
        let rowTop = buttons[0].offsetTop;

        for (let index = 1; index <= buttons.length; index += 1) {
            const top = index < buttons.length ? buttons[index].offsetTop : null;
            if (top !== null && top === rowTop) continue;

            const rowCount = index - rowStart;
            if (rowStart > 0 && rowCount < columns) {
                const offset = (columns - rowCount) * pitch;
                buttons[rowStart].classList.add("header-row-start");
                buttons[rowStart].style.marginLeft = offset + "px";
            }

            rowStart = index;
            if (top !== null) rowTop = top;
        }
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
