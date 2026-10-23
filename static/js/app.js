document.addEventListener("DOMContentLoaded", () => {
    const sidebar = document.querySelector("#sidebar");
    const backdrop = document.querySelector("#sidebarBackdrop");
    const menuButton = document.querySelector("#menuButton");
    const toggleSidebar = (isOpen) => {
        sidebar?.classList.toggle("open", isOpen);
        backdrop?.classList.toggle("open", isOpen);
        menuButton?.setAttribute("aria-expanded", String(isOpen));
    };
    menuButton?.addEventListener("click", () => toggleSidebar(!sidebar.classList.contains("open")));
    backdrop?.addEventListener("click", () => toggleSidebar(false));
    sidebar?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => toggleSidebar(false)));

    const dataElement = document.querySelector("#dashboard-data");
    if (!dataElement || typeof Chart === "undefined") return;
    const data = JSON.parse(dataElement.textContent);
    const orange = "#FF5A1F";
    const green = "#43A77D";
    const grid = "#ECEAE5";
    const text = "#898983";
    Chart.defaults.font.family = "DM Sans, sans-serif";
    Chart.defaults.font.size = 10;
    Chart.defaults.color = text;

    const trendCanvas = document.querySelector("#trendChart");
    if (trendCanvas) {
        new Chart(trendCanvas, {
            type: "line",
            data: {
                labels: data.trend.map((item) => item.bucket),
                datasets: [
                    { label: "Income", data: data.trend.map((item) => item.income), borderColor: green, backgroundColor: `${green}18`, fill: true, tension: 0.35, pointRadius: 3 },
                    { label: "Expenses", data: data.trend.map((item) => item.expenses), borderColor: orange, backgroundColor: `${orange}12`, fill: true, tension: 0.35, pointRadius: 3 },
                ],
            },
            options: { responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 7, padding: 18 } } }, scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: grid }, ticks: { callback: (value) => `₹${value}` } } } },
        });
    }

    const categoryCanvas = document.querySelector("#categoryChart");
    if (categoryCanvas && data.categories.length) {
        const colors = [orange, "#8270A7", "#58A98D", "#E9A23B", "#5684B8", "#D77692"];
        new Chart(categoryCanvas, {
            type: "doughnut",
            data: { labels: data.categories.map((item) => item.category), datasets: [{ data: data.categories.map((item) => item.total), backgroundColor: colors, borderWidth: 3, borderColor: "#FFFFFF", hoverOffset: 6 }] },
            options: { responsive: true, maintainAspectRatio: false, cutout: "68%", plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 7, padding: 12 } }, tooltip: { callbacks: { label: (context) => ` ₹${Number(context.raw).toFixed(2)}` } } } },
        });
    }
});
