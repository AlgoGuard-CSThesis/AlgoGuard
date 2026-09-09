const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../templates/monitor.html'), 'utf8');
// Run the actual polling/control functions against a small DOM and HTTP adapter.
const source = template.slice(
    template.indexOf('    function acceptSession('),
    template.indexOf('    startButton.addEventListener('),
).replace('{{ csrf_token()|tojson }}', '"test-token"');

function snapshot(id, lastSeq, events = [], state = 'running') {
    return {status: 'success', session: {session_id: id, state}, last_seq: lastSeq, events};
}

function harness(respond) {
    const observed = {requests: [], feed: [{seq: 100}], rendered: [], stops: 0};
    const intervals = new Map();
    const timeouts = new Map();
    let nextTimer = 2;
    const context = vm.createContext({
        lastSeq: 100, sessionId: 'old', pollTimer: 1, pollInFlight: false,
        controlInFlight: false, requestVersion: 0, pollHadError: false,
        buckets: [], pendingBucket: {},
        POLL_INTERVAL_MS: 1000, REQUEST_TIMEOUT_MS: 10000, AbortController,
        document: {createElement: () => ({appendChild() {}})},
        feedBody: {replaceChildren: () => { observed.feed = []; }},
        drawChart() {}, pushBucket() {},
        addFeedRows: events => observed.feed.push(...events),
        renderState: session => {
            observed.rendered.push(session.session_id);
            return session.state === 'running';
        },
        setMessage: message => { observed.message = message; },
        window: {
            clearInterval: id => { observed.stops++; intervals.delete(id); },
            setInterval: callback => {
                const id = nextTimer++;
                intervals.set(id, callback);
                return id;
            },
            clearTimeout: id => { timeouts.delete(id); },
            setTimeout: callback => {
                const id = nextTimer++;
                timeouts.set(id, callback);
                return id;
            },
        },
        fetch: async (url, options) => {
            observed.requests.push({url, options});
            const body = await respond(url, options, observed.requests.length);
            const status = body.httpStatus || (body.status === 'success' ? 200 : 409);
            return {ok: status >= 200 && status < 300, status, json: async () => body};
        },
    });
    vm.runInContext(source, context);
    intervals.set(1, context.poll);
    return {
        context, observed,
        tickPoll: () => intervals.get(context.pollTimer)?.(),
        expireRequests: () => {
            for (const [id, callback] of [...timeouts]) {
                timeouts.delete(id);
                callback();
            }
        },
        timeouts,
    };
}

test('a session restart clears old rows and resumes from the new event cursor', async () => {
    const {context, observed} = harness((_url, _options, count) => (
        count === 1 ? snapshot('new', 3, [{seq: 1}, {seq: 2}, {seq: 3}]) : snapshot('new', 3)
    ));
    await context.poll();
    assert.deepEqual(observed.feed.map(row => row.seq), [1, 2, 3]);
    await context.poll();
    assert.match(observed.requests[1].url, /since=3&session_id=new$/);
});

test('a new session with no events resets the cursor immediately', async () => {
    const {context, observed} = harness(() => snapshot('new', 0, [], 'starting'));
    await context.poll();
    await context.poll();
    assert.match(observed.requests[1].url, /since=0&session_id=new$/);
    assert.deepEqual(observed.feed, []);
});

test('a stopped session keeps polling so another tab can start a new session', async () => {
    const {context, observed} = harness((_url, _options, count) => (
        count === 1 ? snapshot('old', 100, [], 'stopped') : snapshot('new', 1, [{seq: 1}])
    ));
    await context.poll();
    assert.equal(observed.stops, 0);
    await context.poll();
    assert.deepEqual(observed.feed.map(row => row.seq), [1]);
});

test('a delayed status response cannot undo a successful control action', async () => {
    let releaseOldPoll;
    const oldPoll = new Promise(resolve => { releaseOldPoll = resolve; });
    const {context, observed} = harness((_url, options, count) => {
        if (count === 1) return oldPoll;
        if (options.method === 'POST') return snapshot('new', 0, [], 'starting');
        return snapshot('new', 1, [{seq: 1}]);
    });
    const pending = context.poll();
    await context.control('/monitor/start', {});
    releaseOldPoll(snapshot('old', 101, [{seq: 101}]));
    await pending;
    assert.equal(context.sessionId, 'new');
    assert.equal(context.lastSeq, 0);
    assert.deepEqual(observed.rendered, ['new']);
    await context.poll();
    assert.deepEqual(observed.feed.map(row => row.seq), [1]);
});

test('a rejected control action preserves the current feed', async () => {
    const {context, observed} = harness((_url, options) => (
        options.method === 'POST'
            ? {status: 'error', message: 'A monitoring session is already running.'}
            : snapshot('old', 100)
    ));
    await context.control('/monitor/start', {});
    await new Promise(setImmediate);
    assert.equal(context.sessionId, 'old');
    assert.deepEqual(observed.feed.map(row => row.seq), [100]);
});

for (const failure of ['network', 'server']) {
    test(`polling recovers automatically from a temporary ${failure} failure`, async () => {
        const {context, observed, tickPoll, timeouts} = harness((_url, _options, count) => {
            if (count > 1) return snapshot('old', 101, [{seq: 101}]);
            if (failure === 'network') throw new TypeError('Connection lost');
            return {httpStatus: 503, status: 'error', message: 'Temporarily unavailable'};
        });

        await tickPoll();
        assert.equal(context.lastSeq, 100);
        assert.deepEqual(observed.feed.map(row => row.seq), [100]);
        await tickPoll();

        assert.equal(observed.requests.length, 2);
        assert.equal(context.lastSeq, 101);
        assert.deepEqual(observed.feed.map(row => row.seq), [100, 101]);
        assert.equal(observed.message, '');
        assert.equal(timeouts.size, 0);
    });
}

test('an expired login stops polling and keeps the login message visible', async () => {
    const {observed, tickPoll} = harness(() => ({
        httpStatus: 401, status: 'error', message: 'Login required.',
    }));

    await tickPoll();
    await tickPoll();

    assert.equal(observed.requests.length, 1);
    assert.equal(observed.message, 'Login required.');
});

function untilAborted(options) {
    return new Promise((_resolve, reject) => {
        options.signal?.addEventListener('abort', () => reject(new Error('Request timed out')));
    });
}

test('a stalled status request times out and allows the next scheduled poll', async () => {
    const {context, observed, tickPoll, expireRequests} = harness((_url, options, count) => (
        count === 1 ? untilAborted(options) : snapshot('old', 101, [{seq: 101}])
    ));

    const pending = tickPoll();
    expireRequests();
    await new Promise(setImmediate);
    assert.equal(context.pollInFlight, false);
    await pending;
    await tickPoll();
    assert.equal(observed.requests.length, 2);
    assert.equal(context.lastSeq, 101);
});

test('a stalled control request releases the controls and checks status without repeating POST', async () => {
    const {context, observed, expireRequests} = harness((_url, options) => (
        options.method === 'POST' ? untilAborted(options) : snapshot('new', 1, [{seq: 1}])
    ));

    const pending = context.control('/monitor/start', {});
    expireRequests();
    await new Promise(setImmediate);
    assert.equal(context.controlInFlight, false);
    await pending;
    assert.equal(observed.requests.filter(request => request.options.method === 'POST').length, 1);
    assert.equal(context.sessionId, 'new');
    assert.deepEqual(observed.feed.map(row => row.seq), [1]);
});
