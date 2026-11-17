// node scripts/watch_vectoria_support.cjs <existing Codex chat UUID>
// Requires this PC's signed-in Codex CLI and connected Gmail account.
const { spawn, execFile } = require('node:child_process');
const { once } = require('node:events');
const fs = require('node:fs/promises');
const net = require('node:net');
const path = require('node:path');
const { createInterface } = require('node:readline');
const { setTimeout: sleep } = require('node:timers/promises');
const { promisify } = require('node:util');

const root = path.resolve(__dirname, '..');
const statusFile = path.join(root, '.artifacts', 'vectoria-support-watch.json');
const hour = 60 * 60 * 1000;
const codex = process.env.CODEX_APP_RUNTIME_CLI_PATH || 'codex';

async function checkMail() {
    const child = spawn(codex, ['app-server'], {
        cwd: root, windowsHide: true, stdio: ['pipe', 'pipe', 'ignore']
    });
    await once(child, 'spawn');
    const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
    child.stdin.on('error', () => child.kill());
    const timeout = setTimeout(() => child.kill(), 90000);
    let sequence = 0;
    async function rpc(method, params) {
        const id = ++sequence;
        child.stdin.write(JSON.stringify({ id, method, params }) + '\n');
        for (;;) {
            const { done, value } = await lines.next();
            if (done) throw new Error('Codex connection closed');
            const response = JSON.parse(value);
            if (response.id !== id) continue;
            if (response.error) throw new Error('Codex request failed: ' + method);
            return response.result;
        }
    }
    try {
        await rpc('initialize', {clientInfo: {name: 'vectoria_support', version: '1.0'},
            capabilities: {experimentalApi: true}});
        child.stdin.write('{"method":"initialized"}\n');
        // The connector requires a context. No model turn runs in this ephemeral session.
        const { thread } = await rpc('thread/start', {ephemeral: true, sandbox: 'read-only', approvalPolicy: 'never'});
        const messages = [];
        let cursor;
        do {
            const result = await rpc('mcpServer/tool/call', {
                threadId: thread.id, server: 'codex_apps', tool: 'gmail.search_emails',
                arguments: {
                    query: 'in:anywhere after:1794849600 {from:(support@openai.com) from:(support.openai.com) subject:("OpenAI Support")}',
                    max_results: 100, ...(cursor && {next_page_token: cursor})
                }
            });
            if (result.isError || !Array.isArray(result.structuredContent?.emails)) {
                throw new Error('Connected Gmail search unavailable');
            }
            messages.push(...result.structuredContent.emails.map(mail => mail.id));
            cursor = result.structuredContent.next_page_token;
        } while (cursor);
        return messages;
    } finally {
        clearTimeout(timeout);
        child.kill();
    }
}

async function save(state) {
    const temp = statusFile + '.tmp';
    await fs.writeFile(temp, JSON.stringify(state, null, 2) + '\n');
    await fs.rename(temp, statusFile);
}

async function run(chat) {
    if (!/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(chat || '')) {
        throw new Error('Pass the UUID of the existing Vectoria chat');
    }
    // The OS releases this lock when the watcher exits, including after a crash.
    const lock = net.createServer();
    const name = process.platform === 'win32' ? '\\\\.\\pipe\\vectoria-support-' + chat :
        path.join(require('node:os').tmpdir(), 'vectoria-support-' + chat + '.sock');
    await new Promise((resolve, reject) => lock.once('error', reject).listen(name, resolve));
    try {
        await fs.mkdir(path.dirname(statusFile), {recursive: true});
        let state = {seen: []};
        try { state = JSON.parse(await fs.readFile(statusFile, 'utf8')); }
        catch (err) { if (err.code !== 'ENOENT') throw err; }
        state.pid = process.pid;
        await save(state);
        for (;;) {
            if (state.complete) break;
            const delay = Date.parse(state.nextCheck) - Date.now();
            if (delay > 0) await sleep(delay);
            state.lastAttempt = new Date().toISOString();
            state.nextCheck = new Date(Date.now() + hour).toISOString();
            await save(state);
            try {
                const ids = await checkMail();
                state.lastSuccess = new Date().toISOString();
                state.pending = ids.filter(id => !state.seen.includes(id));
                delete state.error;
                await save(state);
                if (state.pending.length) {
                    // Only local, fixed instructions go into the chat. Email text is untrusted.
                    const prompt = 'The hourly Vectoria checker found support mail. Read the pending Gmail message IDs in .artifacts/vectoria-support-watch.json. Check the sender, case and evidence for the November 16 calls at 15:42:51 and 15:42:52 UTC. Reconcile only charges that Support confirms. Vectoria is already enabled with full reserves, and docs/vectoria-workflow-checks.uk.md records the completed UI checks. The later 20:00:52 UTC hold is separate. If this message does not resolve the case, leave the unconfirmed holds in place and return to the hourly watcher. After the original two calls are reconciled and verified, create .artifacts/vectoria-support-finished to stop this checker and report the result.';
                    const runCodex = promisify(execFile);
                    await runCodex(codex, ['exec', 'resume', chat, prompt], {
                        cwd: root, windowsHide: true, maxBuffer: 32 * 1024 * 1024
                    });
                    state.seen = [...new Set([...state.seen, ...state.pending])];
                    state.pending = [];
                    state.complete = await fs.stat(path.join(root, '.artifacts/vectoria-support-finished'))
                        .then(() => true, err => { if (err.code === 'ENOENT') return false; throw err; });
                }
            } catch (err) {
                // Keep pending mail for the next hour if the connection or handoff fails.
                state.error = err.code || err.name;
            }
            await save(state);
        }
    } finally {
        lock.close();
    }
}

if (require.main === module) run(process.argv[2]).catch(err => {
    console.error(err.message);
    process.exitCode = 1;
});
