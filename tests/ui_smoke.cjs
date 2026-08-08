const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const { chromium } = require("playwright");

const installing = process.env.UI_INSTALLING === "1";
const presets = [
    {
        name: "Anima 3 Studio",
        description: "Preset completo para edit e inpaint",
        models_count: 33,
        nodes_count: 37,
        installed: true,
        workflows: [{
            label: "Workflow",
            url: "/api/workflows/anima.json",
            local: true,
            file: "anima.json",
        }],
        pinned: true,
        size_gb: 42,
        modified_at: 1786147200,
    },
    {
        name: "Krea 2 Full",
        description: "Krea com stack completa",
        models_count: 19,
        nodes_count: 5,
        installed: true,
        workflows: [],
        pinned: true,
        size_gb: 35,
        modified_at: 1786060800,
    },
    {
        name: "MiniMax H3 5090",
        description: "Vídeo com áudio estéreo nativo",
        models_count: 6,
        nodes_count: 1,
        installed: false,
        workflows: [],
        pinned: true,
        size_gb: 91,
        modified_at: 1785974400,
    },
    {
        name: "Qwen Image Edit 2511",
        description: "Edição de imagem",
        models_count: 8,
        nodes_count: 2,
        installed: false,
        workflows: [],
        pinned: false,
        size_gb: 15,
        modified_at: 1785888000,
    },
];

const status = {
    running: !installing,
    status: installing ? "starting" : "running",
    port: 8818,
    installing,
    install_status: installing ? "running" : "completed",
    installed_presets: ["Anima 3 Studio", "Krea 2 Full"],
    progress: {
        stages: { models: "fila pronta" },
        done: 2,
        total: 6,
        active: [{
            filename: "model.safetensors",
            current: 1073741824,
            total: 4294967296,
            speed_bps: 104857600,
            backend: "xet",
        }],
        recent: [{ filename: "vae.safetensors", ok: true }],
    },
};

function delay(milliseconds) {
    return new Promise(resolve => setTimeout(resolve, milliseconds));
}

async function waitForServer() {
    for (let attempt = 0; attempt < 30; attempt += 1) {
        try {
            const response = await fetch("http://127.0.0.1:8091/");
            if (response.ok) return;
        } catch {}
        await delay(100);
    }
    throw new Error("Static UI server did not start on port 8091");
}

async function mockApi(page) {
    await page.route("**/api/**", route => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ success: true }),
    }));
    await page.route("**/api/presets", route => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ presets }),
    }));
    await page.route("**/api/status", route => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(status),
    }));
}

async function main() {
    const staticServer = spawn(
        "python3",
        ["-m", "http.server", "8091", "--directory", "web"],
        { stdio: "ignore" },
    );
    let browser;
    try {
        await waitForServer();
        browser = await chromium.launch({
            executablePath: "/usr/bin/google-chrome",
            headless: true,
        });

        const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
        const consoleErrors = [];
        desktop.on("console", message => {
            if (message.type() === "error") consoleErrors.push(message.text());
        });
        await mockApi(desktop);
        await desktop.goto("http://127.0.0.1:8091/", { waitUntil: "networkidle" });
        assert.equal(await desktop.locator(".pinned-card").count(), 3);

        const desktopLayout = await desktop.evaluate(() => ({
            columns: getComputedStyle(document.getElementById("pinned-presets"))
                .gridTemplateColumns.split(" ").length,
            panelWidth: document.getElementById("control-panel").getBoundingClientRect().width,
        }));
        assert.equal(desktopLayout.columns, 3);
        assert.ok(desktopLayout.panelWidth >= 298 && desktopLayout.panelWidth <= 302);

        if (installing) {
            assert.equal(await desktop.locator("#start-btn").textContent(), "INSTALANDO...");
            assert.equal(await desktop.locator("#start-btn").isDisabled(), true);
            assert.equal(await desktop.locator("#cancel-btn").isVisible(), true);
            assert.equal(await desktop.locator("#restart-btn").isDisabled(), true);
            assert.equal(await desktop.locator("#progress-summary").textContent(), "2/6 MODELOS · 33%");
            const activity = await desktop.locator("#activity-list").textContent();
            assert.match(activity, /fila pronta/);
            assert.match(activity, /model\.safetensors/);
            assert.match(activity, /vae\.safetensors/);
        } else {
            await desktop.locator(".pinned-card .preset-checkbox").first().check();
            assert.equal(await desktop.locator("#queue-count").textContent(), "1");
            assert.equal(await desktop.locator("#queue-total").textContent(), "42 GB");
        }

        await desktop.locator("#manage-btn").click();
        assert.equal(await desktop.locator("#manage-dialog").evaluate(dialog => dialog.open), true);
        assert.equal(await desktop.locator(".manage-remove").count(), 2);
        assert.equal(
            await desktop.locator("#manage-dialog").evaluate(dialog => getComputedStyle(dialog).borderRadius),
            "0px",
        );
        if (installing) {
            const disabled = await desktop.locator(".manage-remove").evaluateAll(
                buttons => buttons.every(button => button.disabled),
            );
            assert.equal(disabled, true);
        }

        assert.deepEqual(consoleErrors, []);
        await desktop.screenshot({ path: "/tmp/arrakis-ui-desktop.png", fullPage: true });

        const mobile = await browser.newPage({ viewport: { width: 375, height: 812 } });
        await mockApi(mobile);
        await mobile.goto("http://127.0.0.1:8091/", { waitUntil: "networkidle" });
        assert.equal(await mobile.locator(".recent-row").count(), 1);
        const widths = await mobile.evaluate(() => ({
            page: document.documentElement.scrollWidth,
            viewport: window.innerWidth,
        }));
        assert.equal(widths.page, widths.viewport);
        await mobile.screenshot({ path: "/tmp/arrakis-ui-mobile.png", fullPage: true });
    } finally {
        if (browser) await browser.close();
        staticServer.kill("SIGTERM");
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
