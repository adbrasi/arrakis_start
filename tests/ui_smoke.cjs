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
        workflows: [{ label: "Workflow", url: "/api/workflows/anima.json", local: true, file: "anima.json" }],
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

function createStatus(isInstalling = installing) {
    return {
        running: !isInstalling,
        status: isInstalling ? "starting" : "running",
        port: 8818,
        installing: isInstalling,
        install_status: isInstalling ? "running" : "completed",
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
}

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

function requestRecord(request) {
    return {
        path: new URL(request.url()).pathname,
        method: request.method(),
        body: request.postData(),
    };
}

async function mockApi(page, currentStatus, requests) {
    page.on("request", request => {
        if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(requestRecord(request));
    });
    await page.route("**/api/**", route => {
        const path = new URL(route.request().url()).pathname;
        const response = path === "/api/cancel"
            ? { success: true, cancelled: true }
            : path === "/api/uninstall"
                ? { success: true, deleted: ["model.safetensors"], bytes_freed: 1073741824 }
                : { success: true };
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) });
    });
    await page.route("**/api/presets", route => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ presets }),
    }));
    await page.route("**/api/status", route => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(currentStatus),
    }));
}

async function newAppPage(browser, viewport, currentStatus) {
    const page = await browser.newPage({ viewport });
    page.setDefaultTimeout(2000);
    await page.addInitScript(() => {
        const nativeSetInterval = window.setInterval.bind(window);
        window.setInterval = (handler, milliseconds, ...args) => nativeSetInterval(
            handler,
            milliseconds === 5000 ? 1000 : milliseconds,
            ...args,
        );
    });
    const requests = [];
    const consoleErrors = [];
    page.on("console", message => {
        if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("dialog", dialog => dialog.accept());
    await mockApi(page, currentStatus, requests);
    await page.goto("http://127.0.0.1:8091/", { waitUntil: "domcontentloaded" });
    await page.locator(".pinned-card").first().waitFor({ state: "attached" });
    return { page, requests, consoleErrors };
}

async function waitForText(locator, expected) {
    for (let attempt = 0; attempt < 30; attempt += 1) {
        if (await locator.textContent() === expected) return;
        await delay(50);
    }
    assert.equal(await locator.textContent(), expected);
}

async function clickAndCapturePost(page, selector, path) {
    const target = page.locator(selector).first();
    const request = page.waitForRequest(candidate => (
        new URL(candidate.url()).pathname === path && candidate.method() === "POST"
    ), { timeout: 8000 });
    try {
        await target.click();
        return requestRecord(await request);
    } catch (error) {
        request.catch(() => {});
        console.error("Missing action request:", path, await target.isDisabled(), await target.textContent());
        throw error;
    }
}

function assertActionRequest(request, path, body = null) {
    assert.equal(request.path, path);
    assert.equal(request.method, "POST");
    assert.equal(request.body, body === null ? null : JSON.stringify(body));
}

async function verifyLifecycleRequests(page, requests, currentStatus) {
    await page.locator(".pinned-card .preset-checkbox").first().check();
    assert.equal(await page.locator("#queue-count").textContent(), "1");
    assert.equal(await page.locator("#queue-total").textContent(), "42 GB");
    await page.locator("#extra-flags-input").fill("--disable-xformers --preview-method auto");
    const installRequest = await clickAndCapturePost(page, "#start-btn", "/api/install");
    assertActionRequest(installRequest, "/api/install", {
        presets: ["Anima 3 Studio"],
        extra_flags: ["--disable-xformers", "--preview-method", "auto"],
    });
    currentStatus.installing = true;
    currentStatus.running = false;
    currentStatus.status = "starting";
    currentStatus.install_status = "running";
    assert.equal(await page.locator("#start-btn").textContent(), "INSTALANDO...");
    assert.equal(await page.locator("#cancel-btn").isVisible(), true);

    const cancelRequest = await clickAndCapturePost(page, "#cancel-btn", "/api/cancel");
    assertActionRequest(cancelRequest, "/api/cancel");
    currentStatus.installing = false;
    currentStatus.running = false;
    currentStatus.status = "stopped";
    currentStatus.install_status = "cancelled";
    await page.locator("#cancel-btn").waitFor({ state: "hidden" });
    assert.match(await page.locator("#toast-container").textContent(), /Instalação cancelada/);

    assert.equal(await page.locator("#restart-btn").isDisabled(), false);
    const restartRequest = await clickAndCapturePost(page, "#restart-btn", "/api/restart");
    assertActionRequest(restartRequest, "/api/restart");

    const shutdownRequest = await clickAndCapturePost(page, "#shutdown-btn", "/api/shutdown");
    assertActionRequest(shutdownRequest, "/api/shutdown");

    requests.splice(0, requests.length);
    await page.locator("#manage-btn").click();
    const presetsReload = page.waitForResponse(response => (
        new URL(response.url()).pathname === "/api/presets"
        && response.request().method() === "GET"
    ), { timeout: 8000 });
    const removalToast = page.locator("#toast-container .toast.success").filter({
        hasText: "Anima 3 Studio",
    });
    const uninstallRequest = await clickAndCapturePost(page, ".manage-remove", "/api/uninstall");
    assertActionRequest(uninstallRequest, "/api/uninstall", { preset: "Anima 3 Studio" });
    await removalToast.waitFor({ state: "visible" });
    await presetsReload;
    await delay(350);
    assert.equal(requests.some(request => request.path === "/api/shutdown"), false);
    await page.locator("#manage-close").click();
}

async function verifyTerminalState(browser, installStatus, expectedToast) {
    const currentStatus = createStatus(true);
    const { page } = await newAppPage(browser, { width: 1000, height: 700 }, currentStatus);
    try {
        assert.equal(await page.locator("#cancel-btn").isVisible(), true);
        currentStatus.installing = false;
        currentStatus.running = installStatus === "completed_with_failures";
        currentStatus.status = currentStatus.running ? "running" : "error";
        currentStatus.install_status = installStatus;
        await page.locator("#cancel-btn").waitFor({ state: "hidden" });
        await waitForText(
            page.locator("#status-text"),
            currentStatus.running ? "COMFYUI: RODANDO" : "COMFYUI: ERRO",
        );
        assert.match(await page.locator("#toast-container").textContent(), expectedToast);
    } finally {
        await page.close();
    }
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
        browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", headless: true });

        const currentStatus = createStatus();
        const { page: desktop, requests, consoleErrors } = await newAppPage(
            browser,
            { width: 1440, height: 1000 },
            currentStatus,
        );
        assert.equal(await desktop.locator(".pinned-card").count(), 3);
        assert.equal(await desktop.locator(".pinned-card .preset-pin").count(), 3);

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
            await verifyLifecycleRequests(desktop, requests, currentStatus);
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
        await desktop.close();

        await verifyTerminalState(browser, "failed", /A instalação falhou/);
        await verifyTerminalState(browser, "completed_with_failures", /ComfyUI iniciado, mas alguns itens não baixaram/);

        const { page: mobile } = await newAppPage(
            browser,
            { width: 375, height: 812 },
            createStatus(),
        );
        assert.equal(await mobile.locator(".recent-row").count(), 1);
        const widths = await mobile.evaluate(() => ({
            page: document.documentElement.scrollWidth,
            viewport: window.innerWidth,
        }));
        assert.equal(widths.page, widths.viewport);
        await mobile.screenshot({ path: "/tmp/arrakis-ui-mobile.png", fullPage: true });
        await mobile.close();
    } finally {
        if (browser) await browser.close();
        staticServer.kill("SIGTERM");
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
