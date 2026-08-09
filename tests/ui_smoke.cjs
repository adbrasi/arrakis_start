const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const installing = process.env.UI_INSTALLING === "1";
const webRoot = path.resolve(__dirname, "..", "web");
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

async function verifySuiteStaticServer(suiteServer) {
    const page = await fetch(new URL("/", suiteServer.baseURL));
    assert.equal(page.status, 200);
    assert.match(page.headers.get("content-type") || "", /text\/html/);

    const script = await fetch(new URL("/app.js", suiteServer.baseURL));
    assert.equal(script.status, 200);
    assert.match(script.headers.get("content-type") || "", /javascript/);

    const escaped = await fetch(new URL("/%2e%2e/%2e%2e/etc/passwd", suiteServer.baseURL));
    assert.equal(escaped.status, 404);
}

function staticContentType(filePath) {
    switch (path.extname(filePath).toLowerCase()) {
        case ".css": return "text/css; charset=utf-8";
        case ".html": return "text/html; charset=utf-8";
        case ".js": return "text/javascript; charset=utf-8";
        case ".svg": return "image/svg+xml";
        default: return "application/octet-stream";
    }
}

function sendStaticError(response, statusCode) {
    response.statusCode = statusCode;
    response.end();
}

async function serveStaticFile(request, response) {
    if (request.method !== "GET" && request.method !== "HEAD") {
        response.setHeader("Allow", "GET, HEAD");
        sendStaticError(response, 405);
        return;
    }

    let pathname;
    try {
        pathname = decodeURIComponent(new URL(request.url, "http://127.0.0.1").pathname);
    } catch {
        sendStaticError(response, 400);
        return;
    }
    const relativePath = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
    const filePath = path.resolve(webRoot, relativePath);
    if (filePath !== webRoot && !filePath.startsWith(`${webRoot}${path.sep}`)) {
        sendStaticError(response, 404);
        return;
    }

    let stat;
    try {
        stat = await fs.promises.stat(filePath);
    } catch {
        sendStaticError(response, 404);
        return;
    }
    if (!stat.isFile()) {
        sendStaticError(response, 404);
        return;
    }

    response.statusCode = 200;
    response.setHeader("Content-Type", staticContentType(filePath));
    response.setHeader("Content-Length", stat.size);
    if (request.method === "HEAD") {
        response.end();
        return;
    }
    fs.createReadStream(filePath).on("error", () => {
        if (!response.headersSent) sendStaticError(response, 500);
        else response.destroy();
    }).pipe(response);
}

async function createStaticServer() {
    const connections = new Set();
    const server = http.createServer((request, response) => {
        void serveStaticFile(request, response);
    });
    server.on("connection", socket => {
        connections.add(socket);
        socket.on("close", () => connections.delete(socket));
    });
    await new Promise((resolve, reject) => {
        const onError = error => {
            server.off("listening", onListening);
            reject(error);
        };
        const onListening = () => {
            server.off("error", onError);
            resolve();
        };
        server.once("error", onError);
        server.once("listening", onListening);
        server.listen(0, "127.0.0.1");
    });
    const address = server.address();
    assert.ok(address && typeof address !== "string", "suite server did not expose a TCP address");
    return {
        baseURL: `http://127.0.0.1:${address.port}`,
        async close() {
            for (const socket of connections) socket.destroy();
            await new Promise((resolve, reject) => {
                server.close(error => error ? reject(error) : resolve());
            });
        },
    };
}

function delay(milliseconds) {
    return new Promise(resolve => setTimeout(resolve, milliseconds));
}

function requestRecord(request) {
    return {
        path: new URL(request.url()).pathname,
        method: request.method(),
        body: request.postData(),
    };
}

async function fulfillJson(route, response) {
    await route.fulfill({
        status: response.status ?? 200,
        contentType: "application/json",
        body: JSON.stringify(response.body ?? {}),
    });
}

async function mockApi(page, api, requests) {
    page.on("request", request => {
        if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(requestRecord(request));
    });
    await page.route("**/api/**", async route => {
        const path = new URL(route.request().url()).pathname;
        let response = api.actionResponses?.[path] ?? (path === "/api/cancel"
            ? { success: true, cancelled: true }
            : path === "/api/uninstall"
                ? { success: true, deleted: ["model.safetensors"], bytes_freed: 1073741824 }
                : { success: true });
        if (response?.promise) response = await response.promise;
        if (response?.abort) {
            await route.abort(response.abort);
            response.settled?.resolve();
            return;
        }
        await fulfillJson(route, { status: response.status, body: response.body ?? response });
        response.settled?.resolve();
    });
    await page.route("**/api/presets", route => fulfillJson(
        route,
        api.presetsResponse ?? { body: { presets: api.presets ?? presets } },
    ));
    await page.route("**/api/status", async route => {
        api.statusRequests = (api.statusRequests ?? 0) + 1;
        const response = api.nextStatus
            ? await api.nextStatus(api.statusRequests)
            : { body: api.status };
        if (response?.abort) {
            await route.abort(response.abort);
            response.settled?.resolve();
            return;
        }
        await fulfillJson(route, response);
        response.settled?.resolve();
    });
}

async function newAppPage(browser, baseURL, viewport, api, options = {}) {
    const page = await browser.newPage({ viewport });
    page.setDefaultTimeout(2000);
    await page.addInitScript(({ manualPolling, pollInterval }) => {
        const nativeSetInterval = window.setInterval.bind(window);
        window.setInterval = (handler, milliseconds, ...args) => nativeSetInterval(
            handler,
            milliseconds === 5000 ? pollInterval : milliseconds,
            ...args,
        );
        if (manualPolling) {
            window.setInterval = (handler, milliseconds, ...args) => {
                if (milliseconds === 5000) {
                    window.__triggerArrakisStatusPoll = handler;
                    return 1;
                }
                return nativeSetInterval(handler, milliseconds, ...args);
            };
        }
    }, { manualPolling: Boolean(options.manualPolling), pollInterval: options.pollInterval ?? 500 });
    const requests = [];
    const consoleErrors = [];
    page.on("console", message => {
        if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("dialog", dialog => dialog.accept());
    await mockApi(page, api, requests);
    await page.goto(new URL("/", baseURL).href, { waitUntil: "domcontentloaded" });
    await page.locator(options.readySelector ?? ".pinned-card").first().waitFor({ state: "attached" });
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

async function clickAndAcceptPost(page, selector, path) {
    const response = page.waitForResponse(candidate => (
        new URL(candidate.url()).pathname === path && candidate.request().method() === "POST"
    ), { timeout: 8000 });
    const request = await clickAndCapturePost(page, selector, path);
    await response;
    return request;
}

function assertActionRequest(request, path, body = null) {
    assert.equal(request.path, path);
    assert.equal(request.method, "POST");
    assert.equal(request.body, body === null ? null : JSON.stringify(body));
}

async function waitForCondition(condition, failureMessage) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
        if (await condition()) return;
        await delay(50);
    }
    assert.fail(failureMessage);
}

async function triggerManualStatusPoll(page, times = 1) {
    await page.evaluate(count => {
        for (let index = 0; index < count; index += 1) {
            void window.__triggerArrakisStatusPoll();
        }
    }, times);
}

async function waitForBrowserFrame(page) {
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
}

async function assertInstallingControls(page) {
    assert.equal(await page.locator("#queue-count").textContent(), "1");
    assert.equal(await page.locator("#start-btn").textContent(), "INSTALANDO...");
    assert.equal(await page.locator("#start-btn").isDisabled(), true);
    assert.equal(await page.locator(".preset-checkbox").first().isDisabled(), true);
    assert.equal(await page.locator("#cancel-btn").isVisible(), true);
    assert.equal(await page.locator("#cancel-btn").isDisabled(), false);
}

function createDeferred() {
    let resolve;
    const promise = new Promise(resolvePromise => {
        resolve = resolvePromise;
    });
    return { promise, resolve };
}

async function verifyCatalogInteraction(page) {
    const card = page.locator(".pinned-card").first();
    await card.locator(".preset-description").click();
    assert.equal(await page.locator("#queue-count").textContent(), "1");
    await card.locator(".preset-meta").click();
    assert.equal(await page.locator("#queue-count").textContent(), "0");

    await card.locator(".workflow-link").click();
    assert.equal(await page.locator("#queue-count").textContent(), "0");
}

async function verifyLifecycleRequests(page, requests, currentStatus) {
    await page.locator(".pinned-card .preset-checkbox").first().check();
    assert.equal(await page.locator("#queue-count").textContent(), "1");
    assert.equal(await page.locator("#queue-total").textContent(), "42 GB");
    await page.locator("#extra-flags-input").fill("--disable-xformers --preview-method auto");
    const installRequest = await clickAndAcceptPost(page, "#start-btn", "/api/install");
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

async function verifyTerminalState(browser, baseURL, installStatus, expectedToast) {
    const currentStatus = createStatus(true);
    const { page } = await newAppPage(browser, baseURL, { width: 1000, height: 700 }, { status: currentStatus });
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

async function verifyUnreachableInstallLock(browser, baseURL) {
    const activeStatus = createStatus(true);
    const api = {
        status: activeStatus,
        nextStatus: requestNumber => requestNumber === 1
            ? { body: activeStatus }
            : { status: 503, body: { error: "status offline" } },
    };
    const { page } = await newAppPage(browser, baseURL, { width: 1024, height: 760 }, api);
    try {
        assert.equal(await page.locator("#cancel-btn").isVisible(), true);
        await waitForText(page.locator("#status-text"), "SERVIDOR INACESSÍVEL");
        assert.equal(await page.locator("#start-btn").isDisabled(), true);
        assert.equal(await page.locator(".preset-checkbox").first().isDisabled(), true);
        assert.equal(await page.locator("#restart-btn").isDisabled(), true);
        assert.equal(await page.locator("#shutdown-btn").isDisabled(), false);
        assert.equal(await page.locator("#manage-btn").isDisabled(), true);
        assert.equal(await page.locator("#cancel-btn").isDisabled(), true);
    } finally {
        await page.close();
    }
}

async function verifySerializedPolling(browser, baseURL) {
    const delayedStatus = createDeferred();
    const delayedStatusSettled = createDeferred();
    const initialStatus = createStatus(true);
    const api = {
        status: initialStatus,
        nextStatus: requestNumber => requestNumber === 1
            ? { body: initialStatus }
            : requestNumber === 2
                ? delayedStatus.promise
                : { body: initialStatus },
    };
    const { page } = await newAppPage(
        browser,
        baseURL,
        { width: 1024, height: 760 },
        api,
        { manualPolling: true },
    );
    try {
        await waitForCondition(() => api.statusRequests === 1, "initial status poll did not begin");
        await waitForText(page.locator("#status-text"), "INSTALANDO...");
        await triggerManualStatusPoll(page, 3);
        await waitForCondition(() => api.statusRequests === 2, "deferred status poll did not begin");
        assert.equal(api.statusRequests, 2, "status polling issued a concurrent request");
        delayedStatus.resolve({ body: createStatus(true), settled: delayedStatusSettled });
        await delayedStatusSettled.promise;
        await waitForBrowserFrame(page);
        await triggerManualStatusPoll(page);
        await waitForCondition(() => api.statusRequests === 3, "next manual status poll did not begin");
        assert.equal(await page.locator("#start-btn").isDisabled(), true);
    } finally {
        delayedStatus.resolve({ body: createStatus(true), settled: delayedStatusSettled });
        await page.close();
    }
}

async function verifyLifecycleMutationInvalidatesOlderStatus(browser, baseURL) {
    const preInstallStatus = createDeferred();
    const preInstallStatusSettled = createDeferred();
    const afterInstallStatus = createStatus(true);
    const api = {
        status: createStatus(false),
        nextStatus: requestNumber => {
            if (requestNumber === 1) return { body: createStatus(false) };
            if (requestNumber === 2) return preInstallStatus.promise;
            return { body: afterInstallStatus };
        },
    };
    const { page } = await newAppPage(
        browser,
        baseURL,
        { width: 1024, height: 760 },
        api,
        { manualPolling: true },
    );
    try {
        await waitForCondition(() => api.statusRequests === 1, "initial status poll did not begin");
        await waitForText(page.locator("#status-text"), "COMFYUI: RODANDO");
        await triggerManualStatusPoll(page);
        await waitForCondition(() => api.statusRequests === 2, "pre-install status poll did not begin");
        await page.locator(".pinned-card .preset-checkbox").first().check();
        await clickAndAcceptPost(page, "#start-btn", "/api/install");
        await waitForBrowserFrame(page);
        preInstallStatus.resolve({ body: createStatus(false), settled: preInstallStatusSettled });
        await preInstallStatusSettled.promise;
        await waitForBrowserFrame(page);
        await assertInstallingControls(page);
        assert.equal(api.statusRequests, 2, "a post-acceptance status poll started before the stale-state assertion");
        await triggerManualStatusPoll(page);
        await waitForCondition(() => api.statusRequests === 3, "post-install status poll did not begin");
        await assertInstallingControls(page);
    } finally {
        preInstallStatus.resolve({ body: afterInstallStatus, settled: preInstallStatusSettled });
        await page.close();
    }
}

async function verifyPendingInstallPollCannotApplyOldStatus(browser, baseURL) {
    const installAcceptance = createDeferred();
    const staleStatus = createDeferred();
    const staleStatusSettled = createDeferred();
    const afterInstallStatus = createStatus(true);
    const api = {
        status: createStatus(false),
        actionResponses: { "/api/install": { promise: installAcceptance.promise } },
        nextStatus: requestNumber => requestNumber === 1
            ? { body: createStatus(false) }
            : requestNumber === 2
                ? staleStatus.promise
                : { body: afterInstallStatus },
    };
    const { page } = await newAppPage(
        browser,
        baseURL,
        { width: 1024, height: 760 },
        api,
        { manualPolling: true },
    );
    try {
        await waitForCondition(() => api.statusRequests === 1, "initial status poll did not begin");
        await waitForText(page.locator("#status-text"), "COMFYUI: RODANDO");
        await page.locator(".pinned-card .preset-checkbox").first().check();
        const accepted = page.waitForResponse(response => (
            new URL(response.url()).pathname === "/api/install"
            && response.request().method() === "POST"
        ));
        await clickAndCapturePost(page, "#start-btn", "/api/install");
        await triggerManualStatusPoll(page);
        await waitForCondition(() => api.statusRequests === 2, "status poll did not begin during install POST");
        staleStatus.resolve({ body: createStatus(false), settled: staleStatusSettled });
        await staleStatusSettled.promise;
        await waitForBrowserFrame(page);
        await assertInstallingControls(page);
        installAcceptance.resolve({ body: { success: true } });
        await accepted;
        await waitForBrowserFrame(page);
        await assertInstallingControls(page);
        assert.equal(api.statusRequests, 2, "a post-acceptance status poll started before the stale-state assertion");
        await triggerManualStatusPoll(page);
        await waitForCondition(() => api.statusRequests === 3, "post-acceptance status poll did not begin");
        await assertInstallingControls(page);
    } finally {
        installAcceptance.resolve({ body: { success: true } });
        staleStatus.resolve({ body: afterInstallStatus, settled: staleStatusSettled });
        await page.close();
    }
}

async function verifyPreMutationPollFailureIsIgnored(browser, baseURL) {
    const staleFailure = createDeferred();
    const staleFailureSettled = createDeferred();
    const api = {
        status: createStatus(false),
        nextStatus: requestNumber => requestNumber === 1
            ? { body: createStatus(false) }
            : staleFailure.promise,
    };
    const { page } = await newAppPage(
        browser,
        baseURL,
        { width: 1024, height: 760 },
        api,
        { manualPolling: true },
    );
    try {
        await waitForCondition(() => api.statusRequests === 1, "initial status poll did not begin");
        await waitForText(page.locator("#status-text"), "COMFYUI: RODANDO");
        await page.evaluate(() => { void window.__triggerArrakisStatusPoll(); });
        await waitForCondition(() => api.statusRequests === 2, "pre-mutation status poll did not begin");
        await page.locator(".pinned-card .preset-checkbox").first().check();
        await clickAndAcceptPost(page, "#start-btn", "/api/install");
        staleFailure.resolve({ abort: "failed", settled: staleFailureSettled });
        await staleFailureSettled.promise;
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
        assert.equal(await page.locator("#start-btn").textContent(), "INSTALANDO...");
        assert.equal(await page.locator(".preset-checkbox").first().isDisabled(), true);
        assert.equal(await page.locator("#cancel-btn").isDisabled(), false);
    } finally {
        staleFailure.resolve({ abort: "failed", settled: staleFailureSettled });
        await page.close();
    }
}

async function verifyShutdownStaysLockedAfterStatus(browser, baseURL) {
    const status = createStatus(true);
    const api = { status };
    const { page } = await newAppPage(
        browser,
        baseURL,
        { width: 1024, height: 760 },
        api,
        { manualPolling: true },
    );
    try {
        await clickAndAcceptPost(page, "#shutdown-btn", "/api/shutdown");
        await page.evaluate(() => window.__triggerArrakisStatusPoll());
        await waitForCondition(() => api.statusRequests === 2, "status poll did not finish after shutdown");
        assert.equal(await page.locator("#shutdown-btn").isDisabled(), true);
        assert.match(await page.locator("#shutdown-btn").textContent(), /DESLIGANDO\.\.\./);
    } finally {
        await page.close();
    }
}

async function verifyCancelError(browser, baseURL) {
    const api = {
        status: createStatus(true),
        actionResponses: {
            "/api/cancel": { status: 409, body: { error: "Cancelamento não aceito" } },
        },
    };
    const { page } = await newAppPage(browser, baseURL, { width: 1024, height: 760 }, api);
    try {
        await clickAndCapturePost(page, "#cancel-btn", "/api/cancel");
        await page.locator("#toast-container .toast.error").filter({
            hasText: "Cancelamento não aceito",
        }).waitFor({ state: "visible" });
        assert.doesNotMatch(await page.locator("#toast-container").textContent(), /Nenhuma instalação ativa/);
    } finally {
        await page.close();
    }
}

async function verifyEmptyAndErrorStates(browser, baseURL) {
    const empty = await newAppPage(
        browser,
        baseURL,
        { width: 768, height: 760 },
        { status: createStatus(), presets: [] },
        { readySelector: ".catalog-empty" },
    );
    try {
        assert.match(await empty.page.locator(".catalog-empty").textContent(), /Nenhum preset disponível/);
    } finally {
        await empty.page.close();
    }

    const failed = await newAppPage(
        browser,
        baseURL,
        { width: 768, height: 760 },
        { status: createStatus(), presetsResponse: { status: 500, body: { error: "preset failure" } } },
        { readySelector: "#toast-container .toast.error" },
    );
    try {
        assert.match(await failed.page.locator("#toast-container .toast.error").textContent(), /Falha ao carregar presets/);
    } finally {
        await failed.page.close();
    }
}

async function verifyResponsiveAccessibility(browser, baseURL) {
    for (const width of [375, 768, 1024, 1440]) {
        const { page } = await newAppPage(browser, baseURL, { width, height: 860 }, { status: createStatus() });
        try {
            const dimensions = await page.evaluate(() => {
                const shell = document.querySelector(".app-shell").getBoundingClientRect();
                const panel = document.getElementById("control-panel").getBoundingClientRect();
                const footerButtons = [...document.querySelectorAll(".control-footer button")].map(button => {
                    const rect = button.getBoundingClientRect();
                    const icon = button.querySelector("svg").getBoundingClientRect();
                    return {
                        height: rect.height,
                        fontSize: parseFloat(getComputedStyle(button).fontSize),
                        iconWidth: icon.width,
                        iconHeight: icon.height,
                    };
                });
                return {
                    page: document.documentElement.scrollWidth,
                    viewport: window.innerWidth,
                    shellWidth: shell.width,
                    panelWidth: panel.width,
                    footerButtons,
                };
            });
            assert.equal(dimensions.page, dimensions.viewport, `horizontal overflow at ${width}px`);
            assert.ok(
                Math.abs(dimensions.shellWidth - width) <= 1,
                `shell does not fill ${width}px viewport`,
            );
            if (width > 920) {
                const expectedPanelWidth = Math.min(480, Math.max(360, width * 0.30));
                assert.ok(
                    Math.abs(dimensions.panelWidth - expectedPanelWidth) <= 1,
                    `control panel width is wrong at ${width}px`,
                );
            }
            for (const button of dimensions.footerButtons) {
                assert.ok(button.height >= 68, `footer button is shorter than 68px at ${width}px`);
                assert.equal(button.fontSize, 11);
                assert.equal(button.iconWidth, 20);
                assert.equal(button.iconHeight, 20);
            }

            if (width === 375) {
                await page.locator("#manage-btn").click();
                const touchTargets = await page.evaluate(() => [
                    "#extra-flags-input",
                    ".preset-select",
                    ".preset-checkbox",
                    ".workflow-link",
                    "#start-btn",
                    "#cancel-btn",
                    "#restart-btn",
                    "#shutdown-btn",
                    "#manage-btn",
                    "#manage-close",
                    ".manage-remove",
                ].map(selector => {
                    const element = document.querySelector(selector);
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0
                        ? { selector, width: rect.width, height: rect.height }
                        : null;
                }).filter(Boolean));
                for (const target of touchTargets) {
                    assert.ok(
                        target.width >= 44 && target.height >= 44,
                        `${target.selector} is smaller than a 44px touch target`,
                    );
                }
                assert.equal(
                    await page.getByRole("button", { name: "Remover preset Anima 3 Studio" }).count(),
                    1,
                );
                assert.equal(await page.locator("#manage-dialog").evaluate(dialog => dialog.contains(document.activeElement)), true);
                await page.locator("#manage-close").click();
                assert.equal(await page.locator("#manage-btn").evaluate(button => document.activeElement === button), true);

                const contrast = await page.evaluate(() => {
                    const channel = color => color.match(/\d+(?:\.\d+)?/g).slice(0, 3).map(Number);
                    const luminance = color => channel(color).map(value => {
                        const normalized = value / 255;
                        return normalized <= 0.03928
                            ? normalized / 12.92
                            : ((normalized + 0.055) / 1.055) ** 2.4;
                    }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
                    const ratio = (foreground, background) => {
                        const [high, low] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
                        return (high + 0.05) / (low + 0.05);
                    };
                    const port = document.getElementById("status-port");
                    const input = document.getElementById("extra-flags-input");
                    return {
                        muted: ratio(getComputedStyle(port).color, getComputedStyle(document.getElementById("control-panel")).backgroundColor),
                        placeholder: ratio(getComputedStyle(input, "::placeholder").color, getComputedStyle(input).backgroundColor),
                    };
                });
                assert.ok(contrast.muted >= 4.5, `muted text contrast is ${contrast.muted}`);
                assert.ok(contrast.placeholder >= 4.5, `placeholder contrast is ${contrast.placeholder}`);
            }
        } finally {
            await page.close();
        }
    }

    const reducedMotion = await newAppPage(browser, baseURL, { width: 768, height: 760 }, { status: createStatus() });
    try {
        await reducedMotion.page.emulateMedia({ reducedMotion: "reduce" });
        const transitionDuration = await reducedMotion.page.locator("#progress-fill").evaluate(
            element => getComputedStyle(element).transitionDuration,
        );
        assert.ok(["0.01ms", "1e-05s"].includes(transitionDuration));
    } finally {
        await reducedMotion.page.close();
    }
}

async function main() {
    const staticServer = await createStaticServer();
    let browser;
    try {
        await verifySuiteStaticServer(staticServer);
        browser = await chromium.launch({ executablePath: "/usr/bin/google-chrome", headless: true });

        const currentStatus = createStatus();
        const api = { status: currentStatus };
        const { page: desktop, requests, consoleErrors } = await newAppPage(
            browser,
            staticServer.baseURL,
            { width: 1440, height: 1000 },
            api,
        );
        assert.equal(await desktop.locator(".pinned-card").count(), 3);
        assert.equal(await desktop.locator(".pinned-card .preset-pin").count(), 3);
        if (!installing) await verifyCatalogInteraction(desktop);

        const desktopLayout = await desktop.evaluate(() => ({
            columns: getComputedStyle(document.getElementById("pinned-presets"))
                .gridTemplateColumns.split(" ").length,
            panelWidth: document.getElementById("control-panel").getBoundingClientRect().width,
        }));
        assert.equal(desktopLayout.columns, 3);
        assert.ok(Math.abs(desktopLayout.panelWidth - 432) <= 1);
        await waitForCondition(async () => (
            /fila pronta/.test(await desktop.locator("#activity-list").textContent())
        ), "activity did not render");
        assert.match(await desktop.locator("#activity-list").textContent(), /xet/);

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

        if (installing) {
            assert.equal(await desktop.locator("#manage-btn").isDisabled(), true);
            assert.equal(await desktop.locator("#shutdown-btn").isDisabled(), false);
            const shutdownRequest = await clickAndCapturePost(desktop, "#shutdown-btn", "/api/shutdown");
            assertActionRequest(shutdownRequest, "/api/shutdown");
        } else {
            await desktop.locator("#manage-btn").click();
            assert.equal(await desktop.locator("#manage-dialog").evaluate(dialog => dialog.open), true);
            assert.equal(await desktop.locator(".manage-remove").count(), 2);
            assert.equal(
                await desktop.locator("#manage-dialog").evaluate(dialog => getComputedStyle(dialog).borderRadius),
                "0px",
            );
        }
        if (installing) {
            const disabled = await desktop.locator(".manage-remove").evaluateAll(
                buttons => buttons.every(button => button.disabled),
            );
            assert.equal(disabled, true);
        }
        assert.deepEqual(consoleErrors, []);
        await desktop.screenshot({ path: "/tmp/arrakis-ui-desktop.png", fullPage: true });
        await desktop.close();

        if (!installing) {
            await verifyTerminalState(browser, staticServer.baseURL, "failed", /A instalação falhou/);
            await verifyTerminalState(browser, staticServer.baseURL, "completed_with_failures", /ComfyUI iniciado, mas alguns itens não baixaram/);
            await verifyUnreachableInstallLock(browser, staticServer.baseURL);
            await verifySerializedPolling(browser, staticServer.baseURL);
            await verifyLifecycleMutationInvalidatesOlderStatus(browser, staticServer.baseURL);
            await verifyPendingInstallPollCannotApplyOldStatus(browser, staticServer.baseURL);
            await verifyPreMutationPollFailureIsIgnored(browser, staticServer.baseURL);
            await verifyShutdownStaysLockedAfterStatus(browser, staticServer.baseURL);
            await verifyCancelError(browser, staticServer.baseURL);
            await verifyEmptyAndErrorStates(browser, staticServer.baseURL);
            await verifyResponsiveAccessibility(browser, staticServer.baseURL);
        }

        const { page: mobile } = await newAppPage(
            browser,
            staticServer.baseURL,
            { width: 375, height: 812 },
            { status: createStatus() },
        );
        assert.equal(await mobile.locator(".pinned-card").count(), 3);
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
        await staticServer.close();
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
