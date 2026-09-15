(function () {
    'use strict';

    const setupView = document.getElementById('setup-view');
    const monitorView = document.getElementById('monitor-view');
    const statusBadge = document.getElementById('status-badge');
    const setupForm = document.getElementById('setup-form');
    const formError = document.getElementById('form-error');
    const resetBtn = document.getElementById('reset-btn');

    const profitToggle = document.getElementById('profit_toggle');
    const lossToggle = document.getElementById('loss_toggle');
    const profitFixed = document.getElementById('profit-fixed');
    const profitRange = document.getElementById('profit-range');
    const lossFixed = document.getElementById('loss-fixed');
    const lossRange = document.getElementById('loss-range');

    const configSummary = document.getElementById('config-summary');
    const mSimProgress = document.getElementById('m-sim-progress');
    const mEpochProgress = document.getElementById('m-epoch-progress');
    const mBalance = document.getElementById('m-balance');
    const mPool = document.getElementById('m-pool');
    const mBest = document.getElementById('m-best');
    const mWorst = document.getElementById('m-worst');
    const mLast = document.getElementById('m-last');
    const chartCanvas = document.getElementById('chart');
    const statsPanel = document.getElementById('stats-panel');

    const mStartBtn = document.getElementById('m-start-btn');
    const mStopBtn = document.getElementById('m-stop-btn');
    const mResetBtn = document.getElementById('m-reset-btn');

    let looping = false;
    let lastConfig = null;
    let lastChart = [];
    let lastBalanceInitial = 0;
    let lastStatus = 'idle';

    function fmt(n) {
        if (n === null || n === undefined) return '—';
        return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    profitToggle.addEventListener('change', () => {
        profitFixed.classList.toggle('hidden', profitToggle.checked);
        profitRange.classList.toggle('hidden', !profitToggle.checked);
    });
    lossToggle.addEventListener('change', () => {
        lossFixed.classList.toggle('hidden', lossToggle.checked);
        lossRange.classList.toggle('hidden', !lossToggle.checked);
    });

    resetBtn.addEventListener('click', () => {
        setupForm.reset();
        profitFixed.classList.remove('hidden');
        profitRange.classList.add('hidden');
        lossFixed.classList.remove('hidden');
        lossRange.classList.add('hidden');
        formError.classList.add('hidden');
    });

    function collectConfig() {
        const fd = new FormData(setupForm);
        return {
            balance: fd.get('balance'),
            buy_pct: fd.get('buy_pct'),
            sell_pct: fd.get('sell_pct'),
            spread_pct: fd.get('spread_pct'),
            profit_pct: fd.get('profit_pct'),
            loss_pct: fd.get('loss_pct'),
            total_simulations: fd.get('total_simulations'),
            max_epoch: fd.get('max_epoch'),
            profit_toggle: fd.get('profit_toggle') === 'on',
            profit_amount: fd.get('profit_amount'),
            profit_min: fd.get('profit_min'),
            profit_max: fd.get('profit_max'),
            loss_toggle: fd.get('loss_toggle') === 'on',
            loss_amount: fd.get('loss_amount'),
            loss_min: fd.get('loss_min'),
            loss_max: fd.get('loss_max'),
        };
    }

    async function startSimulation(cfg) {
        formError.classList.add('hidden');
        const res = await fetch('/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cfg),
        });
        const data = await res.json();
        if (!res.ok) {
            formError.textContent = data.error || 'Could not start the simulation.';
            formError.classList.remove('hidden');
            return;
        }
        lastConfig = cfg;
        render(data);
        tickLoop();
    }

    setupForm.addEventListener('submit', (e) => {
        e.preventDefault();
        startSimulation(collectConfig());
    });

    mStartBtn.addEventListener('click', () => {
        if (lastStatus === 'running' || !lastConfig) return;
        startSimulation(lastConfig);
    });

    mStopBtn.addEventListener('click', async () => {
        const res = await fetch('/api/stop', { method: 'POST' });
        const data = await res.json();
        render(data);
    });

    mResetBtn.addEventListener('click', async () => {
        const res = await fetch('/api/reset', { method: 'POST' });
        const data = await res.json();
        render(data);
    });

    async function tickLoop() {
        if (looping) return;
        looping = true;
        try {
            while (true) {
                const res = await fetch('/api/tick', { method: 'POST' });
                const data = await res.json();
                render(data);
                if (data.status !== 'running') break;
            }
        } finally {
            looping = false;
        }
    }

    function configField(label, value) {
        const div = document.createElement('div');
        div.className = 'col-md col-6 field';
        div.innerHTML = `<small class="text-muted d-block">${label}</small><strong>${value}</strong>`;
        return div;
    }

    function renderConfigSummary(s) {
        configSummary.innerHTML = '';
        const profitLabel = s.profit_toggle ? `${fmt(s.profit_min)}–${fmt(s.profit_max)}%` : `${fmt(s.profit_min)}%`;
        const lossLabel = s.loss_toggle ? `${fmt(s.loss_min)}–${fmt(s.loss_max)}%` : `${fmt(s.loss_min)}%`;
        const items = [
            ['Starting balance', fmt(s.balance_initial)],
            ['Buy %', fmt(s.buy_pct)],
            ['Sell %', fmt(s.sell_pct)],
            ['Spread %', fmt(s.spread_pct)],
            ['Profit % (weight)', fmt(s.profit_pct)],
            ['Loss % (weight)', fmt(s.loss_pct)],
            ['Profit amount', profitLabel],
            ['Loss amount', lossLabel],
            ['Simulations (N)', s.total_simulations],
            ['Max epoch', s.max_epoch],
        ];
        for (const [label, value] of items) {
            configSummary.appendChild(configField(label, value));
        }
    }

    function statBox(label, entry, totalRuns) {
        const col = document.createElement('div');
        col.className = 'col-md col-6';
        const body = entry
            ? `<span class="fs-5 fw-bold d-block">${fmt(entry.balance)}</span>` +
              `<small class="text-muted d-block">rank ${entry.rank} of ${totalRuns} (simulation #${entry.run_no})</small>`
            : `<span class="fs-5 fw-bold d-block">—</span>`;
        col.innerHTML = `<div class="card metric-card"><div class="card-body py-2 px-3">` +
            `<small class="text-muted d-block">${label}</small>${body}</div></div>`;
        return col;
    }

    function renderStats(stats) {
        if (!stats) {
            statsPanel.classList.add('hidden');
            statsPanel.innerHTML = '';
            return;
        }
        statsPanel.classList.remove('hidden');
        statsPanel.innerHTML = '';
        statsPanel.appendChild(statBox('Best (rank 1)', stats.best, stats.total));
        statsPanel.appendChild(statBox('Bottom of profit zone', stats.profit_bottom, stats.total));
        statsPanel.appendChild(statBox('Middle (median)', stats.median, stats.total));
        statsPanel.appendChild(statBox('Top of loss zone', stats.loss_top, stats.total));
        statsPanel.appendChild(statBox('Worst (last rank)', stats.worst, stats.total));
    }

    function render(state) {
        lastStatus = state.status;
        lastChart = state.chart || [];
        lastBalanceInitial = state.balance_initial || 0;

        const badgeClass = { idle: 'text-bg-secondary', running: 'text-bg-success', finished: 'text-bg-primary' }[state.status] || 'text-bg-secondary';
        statusBadge.textContent = state.status;
        statusBadge.className = 'badge rounded-pill ' + badgeClass;

        if (state.status === 'idle') {
            setupView.classList.remove('hidden');
            monitorView.classList.add('hidden');
            return;
        }

        setupView.classList.add('hidden');
        monitorView.classList.remove('hidden');
        renderConfigSummary(state);

        mSimProgress.textContent = `${state.current_simulation} / ${state.total_simulations}`;
        mEpochProgress.textContent = `${state.current_epoch} / ${state.max_epoch}`;
        mBalance.textContent = fmt(state.balance);
        mPool.textContent = fmt(state.pool);
        mBest.textContent = fmt(state.best_balance);
        mWorst.textContent = fmt(state.worst_balance);
        if (state.last_is_profit === null || state.last_is_profit === undefined) {
            mLast.textContent = '—';
            mLast.className = 'fs-5 fw-bold';
        } else {
            mLast.textContent = state.last_is_profit ? 'PROFIT' : 'LOSS';
            mLast.className = 'fs-5 fw-bold ' + (state.last_is_profit ? 'text-success' : 'text-danger');
        }

        const running = state.status === 'running';
        mStartBtn.disabled = running;
        mStartBtn.textContent = running ? 'Running…' : 'Start';
        mStopBtn.classList.toggle('hidden', !running);
        mResetBtn.classList.toggle('hidden', running);

        renderStats(state.stats);
        drawChart(lastChart, lastBalanceInitial, state.status);
    }

    const COLOR_GREEN_BRIGHT = '#16f06a';
    const COLOR_GREEN_DIM = 'rgba(22, 240, 106, 0.28)';
    const COLOR_RED_BRIGHT = '#ff3b3b';
    const COLOR_RED_DIM = 'rgba(255, 59, 59, 0.28)';

    function drawChart(points, balanceInitial, status) {
        const ctx = chartCanvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        const rect = chartCanvas.getBoundingClientRect();
        chartCanvas.width = Math.max(1, rect.width * dpr);
        chartCanvas.height = Math.max(1, rect.height * dpr);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, rect.width, rect.height);

        if (!points || points.length < 2) return;

        const balances = points.map((p) => p.balance);
        const minB = Math.min(balanceInitial, ...balances);
        const maxB = Math.max(balanceInitial, ...balances);
        const span = maxB - minB;
        const pad = span > 0 ? span * 0.08 : 1;
        const lo = minB - pad;
        const hi = maxB + pad;

        const w = rect.width;
        const h = rect.height;
        const n = points.length;
        const xAt = (i) => (n === 1 ? w / 2 : (i / (n - 1)) * w);
        const yAt = (b) => h - ((b - lo) / (hi - lo)) * h;

        ctx.strokeStyle = 'rgba(255,255,255,0.18)';
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        const baseY = yAt(balanceInitial);
        ctx.moveTo(0, baseY);
        ctx.lineTo(w, baseY);
        ctx.stroke();
        ctx.setLineDash([]);

        const signOf = (b) => (b >= balanceInitial ? 1 : -1);

        let activeFromIndex = n;
        if (status === 'running') {
            const lastSign = signOf(balances[n - 1]);
            let i = n - 1;
            while (i > 0 && signOf(balances[i - 1]) === lastSign) i--;
            activeFromIndex = i;
        }

        ctx.lineWidth = 2.2;
        for (let i = 1; i < n; i++) {
            const sign = signOf(balances[i]);
            const active = (i - 1) >= activeFromIndex;
            ctx.strokeStyle = sign > 0
                ? (active ? COLOR_GREEN_BRIGHT : COLOR_GREEN_DIM)
                : (active ? COLOR_RED_BRIGHT : COLOR_RED_DIM);
            ctx.beginPath();
            ctx.moveTo(xAt(i - 1), yAt(balances[i - 1]));
            ctx.lineTo(xAt(i), yAt(balances[i]));
            ctx.stroke();
        }
    }

    window.addEventListener('resize', () => {
        if (lastChart.length) drawChart(lastChart, lastBalanceInitial, lastStatus);
    });

    async function poll() {
        if (looping) return;
        const res = await fetch('/api/state');
        const data = await res.json();
        render(data);
        if (data.status === 'running') tickLoop();
    }

    poll();
    setInterval(poll, 2000);
})();
