(function () {
    function initSidebarControls() {
        const body = document.body;
        const handle = document.getElementById("sidebarHandle");
        const collapseBtn = document.getElementById("sidebarCollapse");
        const scrim = document.getElementById("sidebarScrim");

        if (!handle && !collapseBtn && !scrim) {
            return;
        }

        function syncHandleState() {
            const collapsed = body.classList.contains("sidebar-collapsed");
            const label = collapsed ? "Expand sidebar" : "Collapse sidebar";

            [handle, collapseBtn].forEach((button) => {
                if (!button) return;
                button.setAttribute("aria-pressed", String(collapsed));
                button.setAttribute("aria-label", label);
                button.title = label;
            });
        }

        function toggleSidebar(event) {
            event?.preventDefault();
            body.classList.toggle("sidebar-collapsed");
            syncHandleState();
        }

        handle?.addEventListener("click", toggleSidebar);
        collapseBtn?.addEventListener("click", toggleSidebar);
        scrim?.addEventListener("click", () => {
            body.classList.remove("sidebar-open");
        });

        syncHandleState();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initSidebarControls);
    } else {
        initSidebarControls();
    }
})();