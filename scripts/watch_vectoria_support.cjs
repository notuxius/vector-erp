// Run in the active Codex chat with its authenticated Gmail search connector.
// OpenAI confirmed that specialist replies to this case are also sent by email.
const HOUR_MS = 60 * 60 * 1000;
const CASE_START = 1794849600; // 2026-11-16 17:20 UTC, before escalation.

async function watchVectoriaSupport({ searchMail, lastChecked, saveCheck, seen = [],
    now = Date.now, sleep = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
    const known = new Set(seen);
    for (;;) {
        const delay = lastChecked + HOUR_MS - now();
        if (delay > 0) await sleep(delay);
        lastChecked = now();
        await saveCheck(lastChecked);

        // Support sometimes uses a generic subject. The operator reviews candidates
        // before acting. Re-scan the whole case period to catch delayed delivery.
        let nextPage;
        do {
            const result = await searchMail({
                query: `in:anywhere after:${CASE_START} {from:(support@openai.com) from:(support.openai.com) subject:("OpenAI Support")}`,
                max_results: 100,
                ...(nextPage && { next_page_token: nextPage })
            });
            const page = result.structuredContent;
            if (result.isError || !Array.isArray(page?.emails)) {
                throw new Error("Could not check the connected support mailbox");
            }
            const candidates = page.emails.filter(mail => !known.has(mail.id));
            if (candidates.length) {
                return candidates.map(({ id, thread_id, subject }) => ({ id, thread_id, subject }));
            }
            nextPage = page.next_page_token;
        } while (nextPage);
    }
}

module.exports = { watchVectoriaSupport, HOUR_MS };
