/**
 * Outreach Dashboard - LinkedIn (HeyReach) + Campaigns + Message Analysis
 */
(function() {
    const senderSelect = document.getElementById('senderSelect');
    const datePresets = document.getElementById('datePresets');
    const customDates = document.getElementById('customDates');
    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');
    const loadBtn = document.getElementById('loadBtn');

    const DAILY_INITIAL_ROWS = 7;
    const CAMPAIGNS_INITIAL_ROWS = 12;

    const state = {
        rangeDays: 28,
        startDate: null,
        endDate: null,
        senderId: 'all',
        campaigns: [],
        campaignFilter: 'all',
        campaignSort: 'reply',
        performance: null,
        summary: null,
        dailyExpanded: false,
        dailySort: 'date_desc',
        dailyRows: [],
        campaignsExpanded: false,
        leaderboardOrder: 'best',
        leaderboardLimit: 5,
        supabaseConfigured: false,
        supabaseStats: null,
        instantlyConfigured: false,
        instantlyData: null,
        leadsBoard: null,
    };

    let emailChart = null;

    let linkedinChart = null;
    let replyClassChart = null;

    // --- Helpers ---

    function isoDate(d) { return d.toISOString().slice(0, 10); }

    function getDateRange() {
        if (state.rangeDays === 'custom' && state.startDate && state.endDate) {
            return { start_date: state.startDate, end_date: state.endDate };
        }
        const days = typeof state.rangeDays === 'number' ? state.rangeDays : 28;
        const end = new Date();
        const start = new Date();
        start.setDate(start.getDate() - days);
        return { start_date: isoDate(start), end_date: isoDate(end) };
    }

    function setDefaultCustomDates() {
        const end = new Date();
        const start = new Date();
        start.setDate(start.getDate() - 28);
        if (startDateInput) startDateInput.value = isoDate(start);
        if (endDateInput) endDateInput.value = isoDate(end);
    }

    function weekToLabel(w) {
        if (!w) return '';
        const p = String(w).split('-');
        if (p.length >= 3) return p[1] + '/' + p[2];
        return w;
    }

    function dateToLabel(d) {
        if (!d) return '';
        const p = String(d).split('-');
        if (p.length >= 3) {
            const date = new Date(d + 'T00:00:00Z');
            const month = date.toLocaleString('en-US', { month: 'short' });
            return month + ' ' + parseInt(p[2], 10);
        }
        return d;
    }

    function rateClass(rate, type) {
        if (type === 'accept') {
            if (rate >= 30) return 'high';
            if (rate >= 18) return 'med';
            return 'low';
        }
        if (rate >= 20) return 'high';
        if (rate >= 12) return 'med';
        return 'low';
    }

    function senderInitials(name) {
        if (!name) return '?';
        return name.split(' ').filter(p => !p.endsWith('.')).map(p => p[0]).join('').slice(0, 2).toUpperCase();
    }

    function fmtNum(n) { return (n || 0).toLocaleString(); }
    function fmtPct(n) { return (n || 0).toFixed(1) + '%'; }

    function downloadCsv(filename, rows) {
        const csv = rows.map(r => r.map(c => {
            const s = c == null ? '' : String(c);
            return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
        }).join(',')).join('\n');
        const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename; a.click();
        URL.revokeObjectURL(url);
    }

    // --- API ---

    async function fetchJson(url, timeoutMs) {
        const controller = new AbortController();
        const t = setTimeout(() => controller.abort(), timeoutMs || 120000);
        try {
            const resp = await fetch(url, { signal: controller.signal });
            return resp.ok ? await resp.json().catch(() => null) : await resp.json().catch(() => ({ error: resp.statusText }));
        } finally { clearTimeout(t); }
    }

    async function loadSenders() {
        try {
            const data = await fetchJson('/api/senders', 30000);
            if (data && data.senders && senderSelect) {
                senderSelect.innerHTML = data.senders.map(s =>
                    `<option value="${s.id}">${s.name}</option>`
                ).join('');
            }
        } catch (e) { console.warn('senders load failed', e); }
    }

    // --- Render: LinkedIn Weekly ---

    function buildDailyTotals(performance) {
        const senders = performance.senders || {};
        const totals = performance.totals_by_date || {};
        // If backend provided pre-aggregated totals_by_date, use it; otherwise aggregate.
        const byDate = {};
        Object.keys(totals).forEach(d => {
            const t = totals[d];
            byDate[d] = {
                connections_sent: t.sent || 0,
                connections_accepted: t.accepted || 0,
                messages_sent: t.messages || 0,
                message_replies: t.replies || 0,
            };
        });
        if (Object.keys(byDate).length === 0) {
            Object.values(senders).forEach(rows => (rows || []).forEach(r => {
                const d = r.date;
                if (!d) return;
                if (!byDate[d]) byDate[d] = { connections_sent: 0, connections_accepted: 0, messages_sent: 0, message_replies: 0 };
                byDate[d].connections_sent += +r.connections_sent || 0;
                byDate[d].connections_accepted += +r.connections_accepted || 0;
                byDate[d].messages_sent += +r.messages_sent || 0;
                byDate[d].message_replies += +r.message_replies || 0;
            }));
        }
        const dates = Object.keys(byDate).sort();
        return { dates, byDate };
    }

    function buildDailyRows(performance) {
        const { dates, byDate } = buildDailyTotals(performance);
        return dates.map(d => {
            const r = byDate[d] || {};
            const accRate = r.connections_sent > 0 ? (r.connections_accepted / r.connections_sent * 100) : 0;
            const replyRate = r.messages_sent > 0 ? (r.message_replies / r.messages_sent * 100) : 0;
            return {
                date: d,
                connections_sent: r.connections_sent || 0,
                connections_accepted: r.connections_accepted || 0,
                acceptance_rate: accRate,
                messages_sent: r.messages_sent || 0,
                message_replies: r.message_replies || 0,
                reply_rate: replyRate,
            };
        });
    }

    function sortDailyRows(rows) {
        const arr = [...rows];
        switch (state.dailySort) {
            case 'date_asc': arr.sort((a, b) => a.date.localeCompare(b.date)); break;
            case 'sent_desc': arr.sort((a, b) => b.connections_sent - a.connections_sent); break;
            case 'replies_desc': arr.sort((a, b) => b.message_replies - a.message_replies); break;
            case 'reply_rate_desc': arr.sort((a, b) => b.reply_rate - a.reply_rate); break;
            case 'date_desc':
            default:
                arr.sort((a, b) => b.date.localeCompare(a.date));
        }
        return arr;
    }

    function renderLinkedInTable(performance, summary) {
        const table = document.getElementById('linkedinTable');
        if (!table || !performance) return null;
        const { dates, byDate } = buildDailyTotals(performance);
        state.dailyRows = buildDailyRows(performance);

        const s = summary || {};
        const sorted = sortDailyRows(state.dailyRows);
        const totalDays = sorted.length;
        const visible = state.dailyExpanded ? sorted : sorted.slice(0, DAILY_INITIAL_ROWS);

        let html = '<thead><tr><th>Day</th><th>Connections Sent</th><th>Accepted</th><th>Acceptance Rate</th><th>Messages Sent</th><th>Replies</th><th>Reply Rate</th></tr></thead><tbody>';
        html += `<tr class="total"><td>Total · ${totalDays} day${totalDays === 1 ? '' : 's'}</td>
            <td class="num">${fmtNum(s.total_connections_sent)}</td>
            <td class="num">${fmtNum(s.total_connections_accepted)}</td>
            <td class="num">${fmtPct(s.overall_acceptance_rate)}</td>
            <td class="num">${fmtNum(s.total_messages_sent)}</td>
            <td class="num">${fmtNum(s.total_message_replies)}</td>
            <td class="num">${fmtPct(s.overall_reply_rate)}</td></tr>`;

        visible.forEach(r => {
            html += `<tr><td>${dateToLabel(r.date)}</td>
                <td class="num">${fmtNum(r.connections_sent)}</td>
                <td class="num">${fmtNum(r.connections_accepted)}</td>
                <td class="num">${fmtPct(r.acceptance_rate)}</td>
                <td class="num">${fmtNum(r.messages_sent)}</td>
                <td class="num">${fmtNum(r.message_replies)}</td>
                <td class="num">${fmtPct(r.reply_rate)}</td></tr>`;
        });
        html += '</tbody>';
        table.innerHTML = html;

        // Show-more button
        const showMoreWrap = document.getElementById('dailyShowMore');
        const showMoreLabel = document.getElementById('dailyShowMoreLabel');
        const showMoreBtn = document.getElementById('dailyShowMoreBtn');
        const rowCountEl = document.getElementById('dailyRowCount');
        if (rowCountEl) rowCountEl.textContent = `Showing ${visible.length} of ${totalDays} days`;
        if (showMoreWrap) {
            if (totalDays > DAILY_INITIAL_ROWS) {
                showMoreWrap.style.display = 'flex';
                if (state.dailyExpanded) {
                    showMoreLabel.textContent = `Show fewer days`;
                    showMoreBtn.classList.add('expanded');
                } else {
                    showMoreLabel.textContent = `Show all ${totalDays} days`;
                    showMoreBtn.classList.remove('expanded');
                }
            } else {
                showMoreWrap.style.display = 'none';
            }
        }

        return { dates, byDate };
    }

    function rerenderDailyOnly() {
        // Re-render table without re-fetching
        if (!state.performance || !state.summary) return;
        renderLinkedInTable(state.performance, state.summary);
    }

    function renderLinkedInChart(dates, byDate) {
        const canvas = document.getElementById('linkedinChart');
        if (!canvas || !dates || !dates.length) return;
        if (linkedinChart) linkedinChart.destroy();
        const ctx = canvas.getContext('2d');

        // Show every Nth label on long ranges to avoid x-axis crowding
        const skip = dates.length > 60 ? Math.ceil(dates.length / 12) : dates.length > 20 ? 3 : 1;

        linkedinChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: dates.map(dateToLabel),
                datasets: [
                    { label: 'Connections Sent', data: dates.map(d => (byDate[d] || {}).connections_sent || 0),
                      borderColor: '#8b5cf6', backgroundColor: 'rgba(139, 92, 246, 0.06)', fill: true, tension: 0.3,
                      borderWidth: 2, pointRadius: dates.length > 30 ? 0 : 3, pointHoverRadius: 5,
                      pointBackgroundColor: '#fff', pointBorderColor: '#8b5cf6', pointBorderWidth: 2 },
                    { label: 'Accepted', data: dates.map(d => (byDate[d] || {}).connections_accepted || 0),
                      borderColor: '#059669', backgroundColor: 'rgba(5, 150, 105, 0.08)', fill: true, tension: 0.3,
                      borderWidth: 2.5, pointRadius: dates.length > 30 ? 0 : 3, pointHoverRadius: 5,
                      pointBackgroundColor: '#fff', pointBorderColor: '#059669', pointBorderWidth: 2 },
                    { label: 'Replies', data: dates.map(d => (byDate[d] || {}).message_replies || 0),
                      borderColor: '#2563eb', backgroundColor: 'rgba(37, 99, 235, 0.06)', fill: true, tension: 0.3,
                      borderWidth: 2.5, pointRadius: dates.length > 30 ? 0 : 3, pointHoverRadius: 5,
                      pointBackgroundColor: '#fff', pointBorderColor: '#2563eb', pointBorderWidth: 2 }
                ]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { position: 'top', align: 'end', labels: { boxWidth: 8, boxHeight: 8, usePointStyle: true, pointStyle: 'circle', padding: 16, font: { size: 12, family: 'Inter' } } },
                    tooltip: { backgroundColor: '#0a0d14', padding: 12, cornerRadius: 8, titleFont: { family: 'Inter', size: 12, weight: '600' }, bodyFont: { family: 'JetBrains Mono', size: 12 }, displayColors: true, boxPadding: 4 }
                },
                scales: {
                    x: { grid: { display: false }, ticks: { font: { family: 'Inter', size: 11 }, color: '#8a93a3', autoSkip: true, maxTicksLimit: 14, callback: function(val, idx) { return idx % skip === 0 ? this.getLabelForValue(val) : ''; } } },
                    y: { beginAtZero: true, grid: { color: '#e6e8ec' }, ticks: { font: { family: 'JetBrains Mono', size: 11 }, color: '#8a93a3' } }
                }
            }
        });
    }

    // --- Render: KPI strip ---

    function renderKpis(summary, campaigns) {
        const activeCount = (campaigns || []).filter(c => (c.status || '').toLowerCase() === 'active' || (c.status || '').toLowerCase() === 'in_progress').length;
        document.getElementById('kpiCampaigns').textContent = activeCount;
        document.getElementById('kpiSent').textContent = fmtNum(summary?.total_connections_sent);
        document.getElementById('kpiAcceptRate').textContent = fmtPct(summary?.overall_acceptance_rate);
        document.getElementById('kpiMessages').textContent = fmtNum(summary?.total_messages_sent);
        document.getElementById('kpiReplies').textContent = fmtNum(summary?.total_message_replies);
        document.getElementById('kpiReplyRate').textContent = fmtPct(summary?.overall_reply_rate);
    }

    // --- Render: Insights ---

    function renderInsights(campaigns) {
        // Use any campaign with activity (sent OR accepted OR replies)
        const usable = (campaigns || []).filter(c =>
            (c.sent || 0) + (c.accepted || 0) + (c.replies || 0) > 0
        );
        if (!usable.length) {
            document.getElementById('insightTopTitle').textContent = 'No campaign activity yet';
            document.getElementById('insightTopBody').textContent = 'No campaigns had activity in this period. Try widening your date range.';
            document.getElementById('insightAlertTitle').textContent = 'All clear';
            document.getElementById('insightAlertBody').textContent = 'Nothing to flag.';
            return;
        }

        // Top Performer is identified by MOVED-TO-EMAIL outcomes (client's KPI),
        // sourced from Supabase per-campaign aggregation. Falls back to reply
        // rate only if no moved-to-email data is available yet.
        const movedRanking = (state.leadsBoard && state.leadsBoard.top_campaigns) || [];
        const topMoved = movedRanking.find(c => (c.moved_to_email || 0) > 0);

        if (topMoved) {
            document.getElementById('insightTopTitle').textContent = topMoved.campaign || 'Untitled campaign';
            document.getElementById('insightTopBody').innerHTML =
                `<strong>${fmtNum(topMoved.moved_to_email)}</strong> moved to email — the most of any campaign this period.`;
        } else {
            // Fallback: highest reply rate (no moved-to-email outcomes yet)
            const withMessages = usable.filter(c => (c.messages || 0) > 0);
            const sortedByReply = [...(withMessages.length ? withMessages : usable)]
                .sort((a, b) => (b.replyRate || 0) - (a.replyRate || 0));
            const top = sortedByReply[0];
            document.getElementById('insightTopTitle').textContent = top.name || 'Untitled campaign';
            document.getElementById('insightTopBody').innerHTML =
                `No campaigns have moved a lead to email yet. By reply rate: <strong>${fmtPct(top.replyRate)}</strong> on <strong>${fmtNum(top.sent)}</strong> sent.`;
        }

        // Underperformer still uses reply rate (lowest among those that sent messages)
        const withMessages = usable.filter(c => (c.messages || 0) > 0);
        const sortedByReply = [...(withMessages.length ? withMessages : usable)]
            .sort((a, b) => (b.replyRate || 0) - (a.replyRate || 0));

        // Underperformer = lowest reply rate among campaigns that sent messages
        // (exclude the reply-rate leader so we don't flag the same campaign twice)
        const replyLeader = sortedByReply[0];
        const candidates = sortedByReply.filter(c => c !== replyLeader && (c.messages || 0) >= 5);
        const bottom = candidates.length ? candidates[candidates.length - 1] : null;

        if (bottom) {
            document.getElementById('insightAlertTitle').textContent = bottom.name || 'Untitled campaign';
            document.getElementById('insightAlertBody').innerHTML =
                `Only <strong>${fmtPct(bottom.replyRate)}</strong> reply rate on <strong>${fmtNum(bottom.messages)}</strong> messages sent. Consider rewriting copy or pausing.`;
        } else {
            document.getElementById('insightAlertTitle').textContent = 'All clear';
            document.getElementById('insightAlertBody').textContent = 'No underperforming campaigns to flag yet — all active campaigns are performing within range.';
        }
    }

    // --- Render: Campaigns ---

    function inferTier(c) {
        const rate = c.replyRate || 0;
        if (rate >= 20) return 's';
        if (rate >= 14) return '1';
        if (rate >= 8) return '2';
        if (rate >= 4) return '3';
        return '4';
    }

    function inferStatus(raw) {
        const s = (raw || '').toLowerCase();
        if (s === 'in_progress' || s === 'active' || s === 'running') return 'active';
        if (s === 'paused' || s === 'pause') return 'paused';
        if (s === 'completed' || s === 'finished' || s === 'archived') return 'completed';
        if (s === 'draft' || s === 'created') return 'draft';
        return 'active';
    }

    function isCampaignActiveInPeriod(c) {
        return (c.sent || 0) + (c.accepted || 0) + (c.messages || 0) + (c.replies || 0) > 0;
    }

    function applyCampaignFilter(campaigns, filter) {
        const enriched = campaigns.map(c => ({
            ...c,
            _status: inferStatus(c.status),
            _tier: inferTier(c),
            _activeInPeriod: isCampaignActiveInPeriod(c),
        }));

        // Compute "best" / "underperforming" thresholds across campaigns with activity
        const withActivity = enriched.filter(c => c._activeInPeriod);
        const sortedByReply = [...withActivity].sort((a, b) => (b.replyRate || 0) - (a.replyRate || 0));
        const topCount = Math.max(Math.ceil(sortedByReply.length * 0.25), 1);
        const bottomCount = Math.max(Math.ceil(sortedByReply.length * 0.25), 1);
        const bestSet = new Set(sortedByReply.slice(0, topCount).map(c => c.id));
        const worstSet = new Set(sortedByReply.slice(-bottomCount).map(c => c.id));

        switch (filter) {
            case 'active': return enriched.filter(c => c._status === 'active');
            case 'paused': return enriched.filter(c => c._status === 'paused');
            case 'completed': return enriched.filter(c => c._status === 'completed');
            case 'best': return enriched.filter(c => bestSet.has(c.id));
            case 'underperforming': return enriched.filter(c => worstSet.has(c.id) && c._activeInPeriod);
            case 'all':
            default: return enriched;
        }
    }

    function sortCampaigns(list) {
        const arr = [...list];
        switch (state.campaignSort) {
            case 'reply_asc': arr.sort((a, b) => (a.replyRate || 0) - (b.replyRate || 0)); break;
            case 'acceptance': arr.sort((a, b) => (b.acceptanceRate || 0) - (a.acceptanceRate || 0)); break;
            case 'sent': arr.sort((a, b) => (b.sent || 0) - (a.sent || 0)); break;
            case 'name': arr.sort((a, b) => (a.name || '').localeCompare(b.name || '')); break;
            case 'recent':
                arr.sort((a, b) => {
                    const ad = a.started_at ? new Date(a.started_at).getTime() : 0;
                    const bd = b.started_at ? new Date(b.started_at).getTime() : 0;
                    return bd - ad;
                });
                break;
            case 'reply':
            default: arr.sort((a, b) => (b.replyRate || 0) - (a.replyRate || 0));
        }
        return arr;
    }

    // Parse campaign name into structured parts.
    // Convention: "OSPRI061 - AV - MyConsultantCentral - Search - Consultants - 1-10 - Mixed Tiers"
    function parseCampaignName(name) {
        if (!name) return { code: '', source: '', audience: '', size: '' };
        const parts = String(name).split(' - ').map(p => p.trim());
        const code = parts[0] || '';
        const offset = (parts[1] && /^[A-Z]{1,3}$/.test(parts[1])) ? 2 : 1;
        return {
            code,
            source: parts[offset] || '',
            audience: parts[offset + 2] || parts[offset + 1] || '',
            size: parts[offset + 3] || parts[offset + 2] || '',
        };
    }

    function aggregateBy(campaigns, getter) {
        const map = new Map();
        campaigns.forEach(c => {
            const key = (getter(c) || 'Unknown').trim() || 'Unknown';
            if (!map.has(key)) map.set(key, { name: key, count: 0, sent: 0, accepted: 0, messages: 0, replies: 0 });
            const row = map.get(key);
            row.count += 1;
            row.sent += c.sent || 0;
            row.accepted += c.accepted || 0;
            row.messages += c.messages || 0;
            row.replies += c.replies || 0;
        });
        return Array.from(map.values()).map(r => ({
            ...r,
            acceptanceRate: r.sent > 0 ? (r.accepted / r.sent * 100) : 0,
            replyRate: r.messages > 0 ? (r.replies / r.messages * 100) : 0,
        })).sort((a, b) => b.replyRate - a.replyRate || b.replies - a.replies);
    }

    function renderDimensionRows(containerId, rows) {
        const el = document.getElementById(containerId);
        if (!el) return;
        if (!rows.length) { el.innerHTML = '<div class="empty-text" style="padding:12px;text-align:center;">No data</div>'; return; }
        const max = Math.max(...rows.map(r => r.replyRate), 1);
        el.innerHTML = rows.slice(0, 6).map(r => {
            const widthPct = Math.max((r.replyRate / max) * 100, 2);
            const plural = r.count === 1 ? '' : 's';
            return '<div class="dim-row">'
                + '<div>'
                +   '<div class="dim-row-name" title="' + escapeHtml(r.name) + '">' + escapeHtml(r.name) + '</div>'
                +   '<div class="dim-row-meta">' + r.count + ' campaign' + plural + ' &middot; ' + fmtNum(r.sent) + ' sent &middot; ' + fmtNum(r.replies) + ' replies</div>'
                + '</div>'
                + '<div class="dim-row-stats">'
                +   '<span class="dim-stat-acc" title="Acceptance rate">' + fmtPct(r.acceptanceRate) + '</span>'
                +   '<span class="dim-stat-rep" title="Reply rate">' + fmtPct(r.replyRate) + '</span>'
                + '</div>'
                + '<div class="dim-row-bar">'
                +   '<div class="dim-bar-fill" style="width:' + widthPct + '%; background: linear-gradient(90deg, var(--blue) 0%, #60a5fa 100%);"></div>'
                + '</div>'
                + '</div>';
        }).join('');
    }

    function renderQuickInsights(active) {
        const withMessages = active.filter(c => (c.messages || 0) > 0);
        const byReply = [...withMessages].sort((a, b) => (b.replyRate || 0) - (a.replyRate || 0));
        const bestCopy = byReply[0];
        const worstCopy = byReply.length > 1 ? byReply[byReply.length - 1] : null;

        const withSent = active.filter(c => (c.sent || 0) > 0);
        const byAccept = [...withSent].sort((a, b) => (b.acceptanceRate || 0) - (a.acceptanceRate || 0));
        const bestTarget = byAccept[0];
        const worstTarget = byAccept.length > 1 ? byAccept[byAccept.length - 1] : null;

        const setQI = (valueId, nameId, item, rateField) => {
            const valueEl = document.getElementById(valueId);
            const nameEl = document.getElementById(nameId);
            if (!valueEl || !nameEl) return;
            if (!item) { valueEl.textContent = '—'; nameEl.textContent = 'No data yet'; return; }
            valueEl.textContent = fmtPct(item[rateField] || 0);
            nameEl.textContent = item.name || 'Untitled';
            nameEl.title = item.name || '';
        };
        setQI('qiBestCopyValue', 'qiBestCopyName', bestCopy, 'replyRate');
        setQI('qiBestTargetValue', 'qiBestTargetName', bestTarget, 'acceptanceRate');
        setQI('qiWorstCopyValue', 'qiWorstCopyName', worstCopy, 'replyRate');
        setQI('qiWorstTargetValue', 'qiWorstTargetName', worstTarget, 'acceptanceRate');
    }

    function renderLeaderboardTable(filtered) {
        const sorted = sortCampaigns(filtered);
        const visible = state.campaignsExpanded ? sorted : sorted.slice(0, CAMPAIGNS_INITIAL_ROWS);

        const table = document.getElementById('campaignsTable');
        if (!table) return;
        if (!visible.length) {
            table.innerHTML = '<thead><tr><th>Campaign</th></tr></thead><tbody><tr><td style="color:var(--text-subtle);text-align:center;padding:24px;">No campaigns to show.</td></tr></tbody>';
            const sm = document.getElementById('campaignsShowMore'); if (sm) sm.style.display = 'none';
            return;
        }

        let html = '<thead><tr><th>Campaign</th><th>Status</th><th>Sent</th><th>Accepted</th><th>Acceptance</th><th>Replies</th><th>Reply Rate</th></tr></thead><tbody>';
        visible.forEach(c => {
            const accClass = c.acceptanceRate >= 30 ? 'high' : c.acceptanceRate >= 18 ? 'med' : 'low';
            const repClass = c.replyRate >= 20 ? 'high' : c.replyRate >= 10 ? 'med' : 'low';
            const accColor = accClass === 'high' ? 'var(--emerald)' : accClass === 'med' ? 'var(--amber)' : 'var(--red)';
            const repColor = repClass === 'high' ? 'var(--emerald)' : repClass === 'med' ? 'var(--amber)' : 'var(--red)';
            const statusBadge = '<span class="status ' + c._status + '">' + c._status + '</span>';
            const fullName = c.name || 'Untitled';
            const truncName = fullName.length > 60 ? fullName.slice(0, 60) + '…' : fullName;
            html += '<tr>'
                + '<td title="' + escapeHtml(fullName) + '">' + escapeHtml(truncName) + '</td>'
                + '<td>' + statusBadge + '</td>'
                + '<td class="num">' + fmtNum(c.sent) + '</td>'
                + '<td class="num">' + fmtNum(c.accepted) + '</td>'
                + '<td class="num" style="color:' + accColor + ';">' + fmtPct(c.acceptanceRate) + '</td>'
                + '<td class="num">' + fmtNum(c.replies) + '</td>'
                + '<td class="num" style="color:' + repColor + ';">' + fmtPct(c.replyRate) + '</td>'
                + '</tr>';
        });
        html += '</tbody>';
        table.innerHTML = html;

        const showMoreWrap = document.getElementById('campaignsShowMore');
        const showMoreLabel = document.getElementById('campaignsShowMoreLabel');
        const showMoreBtn = document.getElementById('campaignsShowMoreBtn');
        if (showMoreWrap && sorted.length > CAMPAIGNS_INITIAL_ROWS) {
            showMoreWrap.style.display = 'flex';
            if (state.campaignsExpanded) {
                showMoreLabel.textContent = 'Show less';
                showMoreBtn.classList.add('expanded');
            } else {
                showMoreLabel.textContent = 'Show all ' + sorted.length + ' campaigns';
                showMoreBtn.classList.remove('expanded');
            }
        } else if (showMoreWrap) {
            showMoreWrap.style.display = 'none';
        }
    }

    function renderCampaigns() {
        const empty = document.getElementById('campaignsEmpty');
        const loading = document.getElementById('campaignsLoading');
        const analysis = document.getElementById('campaignsAnalysis');
        if (loading) loading.style.display = 'none';

        const enrichedAll = state.campaigns.map(c => ({
            ...c,
            _status: inferStatus(c.status),
            _activeInPeriod: isCampaignActiveInPeriod(c),
        }));
        const active = enrichedAll.filter(c => c._activeInPeriod);

        const tabCountEl = document.getElementById('tabCountCampaigns');
        if (tabCountEl) tabCountEl.textContent = active.length;

        if (!active.length) {
            if (analysis) analysis.style.display = 'none';
            if (empty) empty.style.display = 'block';
            return;
        }
        if (empty) empty.style.display = 'none';
        if (analysis) analysis.style.display = 'block';

        renderQuickInsights(active);

        const withParsed = active.map(c => ({ ...c, _parsed: parseCampaignName(c.name) }));
        renderDimensionRows('dimSource', aggregateBy(withParsed, c => c._parsed.source));
        renderDimensionRows('dimAudience', aggregateBy(withParsed, c => c._parsed.audience));
        renderDimensionRows('dimSize', aggregateBy(withParsed, c => c._parsed.size));

        const filtered = applyCampaignFilter(state.campaigns, state.campaignFilter)
            .filter(c => c._activeInPeriod || state.campaignFilter !== 'all');
        renderLeaderboardTable(filtered);
    }

        function escapeHtml(s) {
        return String(s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
    }

    // --- Render: Message Analysis ---

    function renderMessageAnalysis(summary, campaigns) {
        const totalReplies = summary?.total_message_replies || 0;
        const messagesSent = summary?.total_messages_sent || 0;

        // Default placeholders (used when Supabase not configured)
        const setKpi = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
        setKpi('kpiBooked', '—');
        setKpi('kpiPositive', '—');
        document.getElementById('kpiBookedHint').textContent = 'Connect Supabase + n8n to track meetings booked';
        document.getElementById('kpiPositiveHint').textContent = 'Connect Supabase + Gemini to classify reply sentiment';

        setKpi('kpiConvos', fmtNum(totalReplies));
        setKpi('kpiTotalReplies', fmtNum(totalReplies));
        document.getElementById('kpiRepliesHint').textContent =
            messagesSent ? `${fmtPct(totalReplies / messagesSent * 100)} reply rate overall` : 'No messages sent yet';

        // If Supabase is configured, fetch real stats and overwrite placeholders
        if (state.supabaseConfigured && state.supabaseStats) {
            const s = state.supabaseStats;
            setKpi('kpiBooked', fmtNum(s.meetings_booked));
            document.getElementById('kpiBookedHint').textContent =
                s.meetings_booked > 0 ? `Detected from ${s.interested} interested replies` : 'No bookings detected yet';
            // Remove "v1.1" tag if present
            const bookedLabel = document.querySelector('#kpiBooked')?.parentElement?.querySelector('.kpi-label .kpi-tag');
            if (bookedLabel) bookedLabel.remove();
            const bookedKpi = document.getElementById('kpiBooked')?.parentElement;
            if (bookedKpi) { bookedKpi.classList.remove('pending'); bookedKpi.classList.remove('accent-gray'); bookedKpi.classList.add('accent-emerald'); }

            setKpi('kpiPositive', fmtNum(s.interested));
            const totalConvos = s.total_conversations || 0;
            document.getElementById('kpiPositiveHint').textContent =
                totalConvos > 0
                    ? `${fmtPct(s.interested / totalConvos * 100)} of ${fmtNum(totalConvos)} conversations`
                    : 'No classified conversations yet';
            const posLabel = document.querySelector('#kpiPositive')?.parentElement?.querySelector('.kpi-label .kpi-tag');
            if (posLabel) posLabel.remove();
            const posKpi = document.getElementById('kpiPositive')?.parentElement;
            if (posKpi) { posKpi.classList.remove('pending'); posKpi.classList.remove('accent-gray'); posKpi.classList.add('accent-blue'); }

            // Open Conversations from Supabase = open + at least one reply
            setKpi('kpiConvos', fmtNum(s.open_conversations));
            setKpi('kpiTotalReplies', fmtNum(s.total_conversations));
            document.getElementById('kpiRepliesHint').textContent =
                `${fmtPct(s.evaluation_coverage)} AI-evaluated`;
        }

        // Reply classification chart
        const canvas = document.getElementById('replyClassChart');
        const placeholder = document.getElementById('replyClassEmpty');
        if (canvas) {
            if (replyClassChart) replyClassChart.destroy();
            const ctx = canvas.getContext('2d');

            let labels, data, colors, hint;
            if (state.supabaseConfigured && state.supabaseStats && state.supabaseStats.total_conversations > 0) {
                const s = state.supabaseStats;
                const interested = s.interested || 0;
                const open = Math.max((s.open_conversations || 0) - interested, 0);
                const closed = Math.max((s.total_conversations || 0) - (s.open_conversations || 0), 0);
                const unevaluated = s.ai_unevaluated || 0;
                labels = ['Interested', 'Open (not yet interested)', 'Closed / Not interested', 'Awaiting AI evaluation'];
                data = [interested, open, closed, unevaluated];
                colors = ['#059669', '#2563eb', '#9ca3af', '#cbd5e1'];
                hint = `${s.total_conversations} conversations · ${s.evaluation_coverage}% AI-evaluated`;
            } else {
                labels = ['Replies (unclassified)', 'No reply'];
                data = [totalReplies, Math.max(messagesSent - totalReplies, 0)];
                colors = ['#2563eb', '#e6e8ec'];
                hint = state.supabaseConfigured
                    ? 'Supabase connected, no conversations stored yet — wire the n8n webhook to start populating'
                    : 'Connect Supabase + Gemini to see interested / open / closed breakdown';
            }
            if (placeholder) placeholder.textContent = hint;

            replyClassChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: labels,
                    datasets: [{
                        data: data,
                        backgroundColor: colors,
                        borderWidth: 0,
                        hoverOffset: 4,
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    cutout: '70%',
                    plugins: {
                        legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'circle', padding: 12, font: { size: 11, family: 'Inter' } } },
                        tooltip: { backgroundColor: '#0a0d14', padding: 12, cornerRadius: 8, titleFont: { family: 'Inter', size: 12, weight: '600' }, bodyFont: { family: 'JetBrains Mono', size: 12 } }
                    }
                }
            });
        }

        // Sequence funnel: HeyReach campaigns aggregate. We approximate steps from connections → accepted → messages → replies → (booked placeholder)
        const sent = summary?.total_connections_sent || 0;
        const accepted = summary?.total_connections_accepted || 0;
        const replies = totalReplies;
        const steps = [
            { label: 'Sent', value: sent, max: sent || 1 },
            { label: 'Accepted', value: accepted, max: sent || 1 },
            { label: 'Messaged', value: messagesSent, max: sent || 1 },
            { label: 'Replied', value: replies, max: sent || 1 },
        ];
        const funnel = document.getElementById('sequenceFunnel');
        if (funnel) {
            funnel.innerHTML = steps.map(step => {
                const pct = step.max > 0 ? (step.value / step.max * 100) : 0;
                return `<div class="funnel-step">
                    <div class="funnel-step-label">${step.label}</div>
                    <div class="funnel-step-bar-wrap">
                        <div class="funnel-step-bar" style="width:${Math.max(pct, 5)}%">${fmtNum(step.value)}</div>
                    </div>
                    <div class="funnel-step-rate">${fmtPct(pct)}</div>
                </div>`;
            }).join('');
        }

        // Sender breakdown (per-sender reply performance)
        const senders = (state.performance || {}).senders || {};
        const senderRows = Object.entries(senders).map(([name, days]) => {
            let sent = 0, accepted = 0, msgs = 0, reps = 0;
            (days || []).forEach(d => {
                sent += +d.connections_sent || 0;
                accepted += +d.connections_accepted || 0;
                msgs += +d.messages_sent || 0;
                reps += +d.message_replies || 0;
            });
            return {
                name,
                sent, accepted, messages: msgs, replies: reps,
                acceptanceRate: sent > 0 ? (accepted / sent * 100) : 0,
                replyRate: msgs > 0 ? (reps / msgs * 100) : 0,
            };
        }).sort((a, b) => b.replies - a.replies);

        const senderGrid = document.getElementById('senderBreakdown');
        if (senderGrid) {
            if (!senderRows.length) {
                senderGrid.innerHTML = '<div class="empty-text" style="text-align:center;padding:16px;">No sender activity in this period.</div>';
            } else {
                senderGrid.innerHTML = senderRows.map(s => `
                    <div class="sender-card">
                        <div class="sender-card-avatar">${senderInitials(s.name)}</div>
                        <div class="sender-card-body">
                            <div class="sender-card-name" title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</div>
                            <div class="sender-card-stats">
                                <span><span class="sender-stat-num">${fmtNum(s.replies)}</span>replies</span>
                                <span><span class="sender-stat-num">${fmtPct(s.replyRate)}</span>rate</span>
                                <span><span class="sender-stat-num">${fmtNum(s.sent)}</span>sent</span>
                            </div>
                        </div>
                    </div>
                `).join('');
            }
        }

        // Campaign leaderboard with order + limit controls
        renderLeaderboard(campaigns);
    }

    function renderLeaderboard(campaigns) {
        const list = document.getElementById('topCampaignsList');
        if (!list) return;
        const filtered = (campaigns || []).filter(c => (c.sent || 0) + (c.replies || 0) > 0);
        const sorted = [...filtered].sort((a, b) => (b.replyRate || 0) - (a.replyRate || 0));
        const ordered = state.leaderboardOrder === 'worst' ? sorted.reverse() : sorted;
        const limit = state.leaderboardLimit === 'all' ? ordered.length : parseInt(state.leaderboardLimit, 10);
        const top = ordered.slice(0, limit);
        if (!top.length) {
            list.innerHTML = '<div class="empty-text" style="text-align:center;padding:24px;">No campaigns with activity to rank yet.</div>';
            return;
        }
        list.innerHTML = top.map((c, i) => {
            const rankClass = state.leaderboardOrder === 'best'
                ? (i === 0 ? 'gold' : i === 1 ? 'silver' : i === 2 ? 'bronze' : '')
                : '';
            return `<div class="top-item">
                <div class="top-rank ${rankClass}">${i + 1}</div>
                <div class="top-name">${escapeHtml(c.name || 'Untitled')}</div>
                <div class="top-stat">
                    <div class="top-stat-value">${fmtPct(c.replyRate)}</div>
                    <div class="top-stat-label">${fmtNum(c.replies)} replies</div>
                </div>
            </div>`;
        }).join('');
    }

    // --- Render: Email Outreach tab (Instantly) ---

    function renderEmailTab() {
        const notConfigured = document.getElementById('emailNotConfigured');
        const loading = document.getElementById('emailLoading');
        const content = document.getElementById('emailContent');
        const empty = document.getElementById('emailEmpty');

        if (!state.instantlyConfigured) {
            if (notConfigured) notConfigured.style.display = 'block';
            if (loading) loading.style.display = 'none';
            if (content) content.style.display = 'none';
            if (empty) empty.style.display = 'none';
            // Reset KPIs
            ['emailKpiAccounts', 'emailKpiSent', 'emailKpiOpenRate', 'emailKpiReplies', 'emailKpiReplyRate', 'emailKpiOpps'].forEach(id => {
                const el = document.getElementById(id);
                if (el) el.textContent = '—';
            });
            return;
        }

        if (notConfigured) notConfigured.style.display = 'none';
        if (loading) loading.style.display = 'none';

        const data = state.instantlyData || {};
        const s = data.summary || {};
        const daily = data.daily || [];
        const camps = data.campaigns || [];

        // KPIs
        const setKpi = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
        setKpi('emailKpiAccounts', fmtNum((data.account_count != null) ? data.account_count : '—'));
        setKpi('emailKpiSent', fmtNum(s.total_sent || 0));
        setKpi('emailKpiOpenRate', fmtPct(s.open_rate || 0));
        setKpi('emailKpiReplies', fmtNum(s.total_unique_replies || s.total_replies || 0));
        setKpi('emailKpiReplyRate', fmtPct(s.reply_rate || 0));
        setKpi('emailKpiOpps', fmtNum(s.total_opportunities || 0));

        // Daily table
        const hasData = (s.total_sent || 0) > 0 || daily.length > 0;
        if (hasData && content) {
            content.style.display = 'block';
            if (empty) empty.style.display = 'none';

            const table = document.getElementById('emailTable');
            if (table) {
                const sorted = [...daily].sort((a, b) => (b.date || '').localeCompare(a.date || ''));
                let html = '<thead><tr><th>Day</th><th>Sent</th><th>Opened</th><th>Open Rate</th><th>Replies</th><th>Reply Rate</th><th>Opportunities</th></tr></thead><tbody>';
                html += `<tr class="total"><td>Total · ${daily.length} day${daily.length === 1 ? '' : 's'}</td>
                    <td class="num">${fmtNum(s.total_sent || 0)}</td>
                    <td class="num">${fmtNum(s.total_unique_opened || 0)}</td>
                    <td class="num">${fmtPct(s.open_rate || 0)}</td>
                    <td class="num">${fmtNum(s.total_unique_replies || 0)}</td>
                    <td class="num">${fmtPct(s.reply_rate || 0)}</td>
                    <td class="num">${fmtNum(s.total_opportunities || 0)}</td></tr>`;
                sorted.slice(0, 7).forEach(d => {
                    const sent = +d.sent || 0;
                    const openRate = sent > 0 ? ((+d.unique_opened || 0) / sent * 100) : 0;
                    const replyRate = sent > 0 ? ((+d.unique_replies || 0) / sent * 100) : 0;
                    html += `<tr><td>${dateToLabel(d.date)}</td>
                        <td class="num">${fmtNum(sent)}</td>
                        <td class="num">${fmtNum(+d.unique_opened || 0)}</td>
                        <td class="num">${fmtPct(openRate)}</td>
                        <td class="num">${fmtNum(+d.unique_replies || 0)}</td>
                        <td class="num">${fmtPct(replyRate)}</td>
                        <td class="num">${fmtNum(+d.opportunities || 0)}</td></tr>`;
                });
                html += '</tbody>';
                table.innerHTML = html;
            }

            // Chart
            const canvas = document.getElementById('emailChart');
            if (canvas && daily.length) {
                const sortedAsc = [...daily].sort((a, b) => (a.date || '').localeCompare(b.date || ''));
                const labels = sortedAsc.map(d => dateToLabel(d.date));
                if (emailChart) emailChart.destroy();
                const ctx = canvas.getContext('2d');
                emailChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels,
                        datasets: [
                            { label: 'Sent', data: sortedAsc.map(d => +d.sent || 0), borderColor: '#8b5cf6', backgroundColor: 'rgba(139, 92, 246, 0.06)', fill: true, tension: 0.3, borderWidth: 2, pointRadius: 0 },
                            { label: 'Opened', data: sortedAsc.map(d => +d.unique_opened || 0), borderColor: '#059669', backgroundColor: 'rgba(5, 150, 105, 0.08)', fill: true, tension: 0.3, borderWidth: 2.5, pointRadius: 0 },
                            { label: 'Replies', data: sortedAsc.map(d => +d.unique_replies || 0), borderColor: '#2563eb', backgroundColor: 'rgba(37, 99, 235, 0.06)', fill: true, tension: 0.3, borderWidth: 2.5, pointRadius: 0 }
                        ]
                    },
                    options: {
                        responsive: true, maintainAspectRatio: false,
                        plugins: {
                            legend: { position: 'top', align: 'end', labels: { boxWidth: 8, boxHeight: 8, usePointStyle: true, pointStyle: 'circle', padding: 16, font: { size: 12, family: 'Inter' } } },
                            tooltip: { backgroundColor: '#0a0d14', padding: 12, cornerRadius: 8 }
                        },
                        scales: {
                            x: { grid: { display: false }, ticks: { font: { family: 'Inter', size: 11 }, color: '#8a93a3', autoSkip: true, maxTicksLimit: 10 } },
                            y: { beginAtZero: true, grid: { color: '#e6e8ec' }, ticks: { font: { family: 'JetBrains Mono', size: 11 }, color: '#8a93a3' } }
                        }
                    }
                });
            }
        } else {
            if (content) content.style.display = 'none';
            if (empty) empty.style.display = 'block';
        }

        // Campaigns table
        const activeCamps = camps.filter(c => (c.sent || 0) > 0);
        const emailTabCount = document.getElementById('emailTabCount');
        if (emailTabCount) emailTabCount.textContent = activeCamps.length;

        const campTable = document.getElementById('emailCampaignsTable');
        const campEmpty = document.getElementById('emailCampaignsEmpty');
        if (!activeCamps.length) {
            if (campTable) campTable.innerHTML = '';
            if (campEmpty) campEmpty.style.display = 'block';
        } else {
            if (campEmpty) campEmpty.style.display = 'none';
            const sortedCamps = [...activeCamps].sort((a, b) => (b.replyRate || 0) - (a.replyRate || 0));
            let html = '<thead><tr><th>Campaign</th><th>Status</th><th>Sent</th><th>Opened</th><th>Open Rate</th><th>Replies</th><th>Reply Rate</th></tr></thead><tbody>';
            sortedCamps.forEach(c => {
                const openClass = c.acceptanceRate >= 40 ? 'high' : c.acceptanceRate >= 20 ? 'med' : 'low';
                const repClass = c.replyRate >= 10 ? 'high' : c.replyRate >= 3 ? 'med' : 'low';
                const openColor = openClass === 'high' ? 'var(--emerald)' : openClass === 'med' ? 'var(--amber)' : 'var(--red)';
                const repColor = repClass === 'high' ? 'var(--emerald)' : repClass === 'med' ? 'var(--amber)' : 'var(--red)';
                const name = (c.name || 'Untitled').length > 60 ? (c.name || '').slice(0, 60) + '…' : (c.name || 'Untitled');
                html += `<tr>
                    <td title="${escapeHtml(c.name || '')}">${escapeHtml(name)}</td>
                    <td><span class="status ${c.status || 'unknown'}">${c.status || 'unknown'}</span></td>
                    <td class="num">${fmtNum(c.sent)}</td>
                    <td class="num">${fmtNum(c.accepted)}</td>
                    <td class="num" style="color:${openColor};">${fmtPct(c.acceptanceRate)}</td>
                    <td class="num">${fmtNum(c.replies)}</td>
                    <td class="num" style="color:${repColor};">${fmtPct(c.replyRate)}</td>
                </tr>`;
            });
            html += '</tbody>';
            if (campTable) campTable.innerHTML = html;
        }
    }

    // --- Render: Leads tab (Kanban) ---

    function fmtRelTime(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d)) return '';
        const diff = (Date.now() - d.getTime()) / 1000;
        if (diff < 3600) return Math.max(1, Math.floor(diff / 60)) + 'm ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        if (diff < 86400 * 30) return Math.floor(diff / 86400) + 'd ago';
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }

    function leadCardHtml(c) {
        const pending = !c.ai_evaluated ? '<span class="lead-pending">pending AI</span>' : '';
        return `<div class="lead-card" data-cid="${escapeHtml(c.conversation_id)}">
            <div class="lead-card-name">${escapeHtml(c.lead_name || 'Unknown')}</div>
            <div class="lead-card-co">${escapeHtml(c.company || '—')}</div>
            ${c.snippet ? `<div class="lead-card-snippet">${escapeHtml(c.snippet)}</div>` : ''}
            <div class="lead-card-foot">
                <span>${escapeHtml(fmtRelTime(c.last_activity))}</span>
                ${pending}
            </div>
        </div>`;
    }

    function renderLeadsTab() {
        const board = state.leadsBoard;
        const notConfigured = document.getElementById('leadsNotConfigured');
        const loading = document.getElementById('leadsLoading');
        const kanban = document.getElementById('kanbanBoard');

        if (loading) loading.style.display = 'none';

        // Reporting strip — HeyReach (state.summary) + Supabase (state.supabaseStats)
        const sum = state.summary || {};
        const ss = state.supabaseStats || {};
        const setK = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        // Connection/reply metrics come from HeyReach summary
        setK('leadKpiConn', fmtNum(sum.total_connections_sent || 0));
        setK('leadKpiAccepted', fmtNum(sum.total_connections_accepted || 0));
        setK('leadKpiAccRate', fmtPct(sum.overall_acceptance_rate || 0));
        setK('leadKpiReplies', fmtNum(sum.total_message_replies || 0));
        setK('leadKpiReplyRate', fmtPct(sum.overall_reply_rate || 0));
        // Open / Interested / Booked KPIs MIRROR the Kanban columns exactly
        // (mutually-exclusive board counts), so the strip and board never disagree.
        const bc = (board && board.counts) ? board.counts : {};
        setK('leadKpiOpen', fmtNum(bc.open != null ? bc.open : (ss.open_conversations || 0)));
        setK('leadKpiInterested', fmtNum(bc.interested != null ? bc.interested : (ss.interested || 0)));
        setK('leadKpiBooked', fmtNum(bc.meeting_booked != null ? bc.meeting_booked : (ss.meetings_booked || 0)));

        if (!board || !board.configured) {
            if (notConfigured) notConfigured.style.display = 'block';
            if (kanban) kanban.style.display = 'none';
            return;
        }
        if (notConfigured) notConfigured.style.display = 'none';
        if (kanban) kanban.style.display = 'grid';

        const cols = board.columns || { open: [], interested: [], meeting_booked: [] };
        const counts = board.counts || {};
        const fill = (bodyId, countId, list) => {
            const body = document.getElementById(bodyId);
            const cnt = document.getElementById(countId);
            if (cnt) cnt.textContent = list.length;
            if (!body) return;
            body.innerHTML = list.length
                ? list.map(leadCardHtml).join('')
                : '<div class="kanban-empty">No leads here yet</div>';
        };
        fill('kanbanOpen', 'kanbanCountOpen', cols.open || []);
        fill('kanbanInterested', 'kanbanCountInterested', cols.interested || []);
        fill('kanbanBooked', 'kanbanCountBooked', cols.meeting_booked || []);

        // Wire card clicks -> drawer
        document.querySelectorAll('#kanbanBoard .lead-card').forEach(card => {
            card.addEventListener('click', () => openLeadDrawer(card.dataset.cid));
        });
    }

    // --- Slide-over drawer ---

    function closeLeadDrawer() {
        const drawer = document.getElementById('leadDrawer');
        const overlay = document.getElementById('leadDrawerOverlay');
        if (drawer) { drawer.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true'); }
        if (overlay) overlay.style.display = 'none';
    }

    async function openLeadDrawer(cid) {
        const drawer = document.getElementById('leadDrawer');
        const overlay = document.getElementById('leadDrawerOverlay');
        const body = document.getElementById('drawerBody');
        if (!drawer) return;
        document.getElementById('drawerName').textContent = 'Loading…';
        document.getElementById('drawerSub').textContent = '';
        if (body) body.innerHTML = '<div class="loading">Loading conversation…</div>';
        if (overlay) overlay.style.display = 'block';
        drawer.classList.add('open');
        drawer.setAttribute('aria-hidden', 'false');

        try {
            const data = await fetchJson('/api/leads/' + encodeURIComponent(cid), 30000);
            const row = data && data.conversation;
            if (!row) { if (body) body.innerHTML = '<div class="kanban-empty">Conversation not found.</div>'; return; }

            const lead = row.lead || {};
            const name = row.lead_full_name || lead.full_name || 'Unknown';
            const company = row.lead_company_name || lead.company_name || '';
            const title = lead.position || lead.title || '';
            const email = lead.email_address || '';
            const profile = lead.profile_url || '';

            document.getElementById('drawerName').textContent = name;
            document.getElementById('drawerSub').textContent =
                [title, company].filter(Boolean).join(' @ ') || email || '';

            const booked = row.is_meeting_booked === true;
            const interested = row.is_interested === true;
            const badgeCls = booked ? 'dbadge-booked' : interested ? 'dbadge-interested' : 'dbadge-open';
            const badgeTxt = booked ? 'Moved To Email' : interested ? 'Interested' : 'Open';
            const conf = (row.ai_confidence != null) ? ` · ${Math.round(row.ai_confidence * 100)}% confidence` : '';

            let html = `<span class="drawer-badge ${badgeCls}">${badgeTxt}${conf}</span>`;
            const contactBits = [];
            if (email) contactBits.push(`<a href="mailto:${escapeHtml(email)}" style="color:var(--blue);text-decoration:none;">${escapeHtml(email)}</a>`);
            if (profile) contactBits.push(`<a href="${escapeHtml(profile)}" target="_blank" rel="noopener" style="color:var(--blue);text-decoration:none;">LinkedIn profile ↗</a>`);
            if (contactBits.length) html += `<div style="font-size:13px;margin-bottom:4px;">${contactBits.join(' &nbsp;·&nbsp; ')}</div>`;

            if (row.ai_reasoning) {
                html += `<div class="drawer-section-label">AI Assessment</div>`;
                html += `<div class="drawer-ai-reason">${escapeHtml(row.ai_reasoning)}</div>`;
            }

            html += `<div class="drawer-section-label">Conversation</div>`;
            const thread = row.conversation_thread || [];
            const senderName = (row.sender_full_name) || (row.sender && row.sender.full_name) || 'Sender';
            const leadName = name;
            const seen = new Set();
            const msgs = [];
            thread.forEach(evt => {
                if (!evt || typeof evt !== 'object') return;
                (evt.recent_messages || []).forEach(m => {
                    if (!m || typeof m !== 'object') return;
                    const txt = (m.message || '').trim();
                    const mtype = (m.message_type || 'Text');
                    const display = txt || (mtype.toLowerCase() !== 'text' ? `[${mtype}]` : '');
                    if (!display) return;
                    const ts = m.creation_time || evt.timestamp || '';
                    const isReply = m.is_reply === true;
                    const key = ts + '|' + (isReply ? 'P' : 'S') + '|' + display;
                    if (seen.has(key)) return;
                    seen.add(key);
                    msgs.push({ ts, isReply, display });
                });
            });
            msgs.sort((a, b) => (a.ts || '').localeCompare(b.ts || ''));
            if (!msgs.length) {
                html += '<div class="kanban-empty">No message history captured</div>';
            } else {
                msgs.forEach(m => {
                    const who = m.isReply ? 'from-prospect' : 'from-sender';
                    const label = m.isReply ? leadName : senderName;
                    html += `<div class="chat-msg ${who}">
                        <div class="chat-bubble">${escapeHtml(m.display)}</div>
                        <div class="chat-meta">${escapeHtml(label)}${m.ts ? ' · ' + escapeHtml(fmtRelTime(m.ts)) : ''}</div>
                    </div>`;
                });
            }
            if (body) body.innerHTML = html;
        } catch (e) {
            if (body) body.innerHTML = '<div class="kanban-empty">Failed to load: ' + escapeHtml(String(e.message || e)) + '</div>';
        }
    }

    // Drawer close wiring
    (function wireDrawer() {
        const closeBtn = document.getElementById('drawerClose');
        const overlay = document.getElementById('leadDrawerOverlay');
        if (closeBtn) closeBtn.addEventListener('click', closeLeadDrawer);
        if (overlay) overlay.addEventListener('click', closeLeadDrawer);
        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLeadDrawer(); });
    })();

    // --- Load all data ---

    async function loadAll() {
        if (loadBtn) loadBtn.disabled = true;
        const range = getDateRange();
        const params = new URLSearchParams(range);
        if (state.senderId !== 'all') params.set('sender_id', state.senderId);

        // Reset expanded states on fresh load
        state.dailyExpanded = false;
        state.campaignsExpanded = false;

        // Hide the initial empty state and reveal the active tab panel
        const initialEl = document.getElementById('initialState');
        if (initialEl) initialEl.style.display = 'none';
        const activeTab = document.querySelector('.tab.active');
        const activeTabName = activeTab ? activeTab.dataset.tab : 'linkedin';
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        const activePanel = document.getElementById('panel-' + activeTabName);
        if (activePanel) activePanel.classList.add('active');

        const setDisplay = (id, val) => { const el = document.getElementById(id); if (el) el.style.display = val; };
        const setHTML = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
        setDisplay('linkedinLoading', 'block');
        setDisplay('linkedinContent', 'none');
        setDisplay('linkedinEmpty', 'none');
        setDisplay('campaignsLoading', 'block');
        setDisplay('campaignsAnalysis', 'none');
        setDisplay('campaignsEmpty', 'none');
        setHTML('campaignsTable', '');

        try {
            const [perfData, campaignsData, msgStats, instantlyData, leadsBoard] = await Promise.all([
                fetchJson('/api/heyreach?' + params, 180000),
                fetchJson('/api/campaigns?' + params, 180000),
                fetchJson('/api/messages/stats?' + params, 60000).catch(() => null),
                fetchJson('/api/instantly/dashboard?' + params, 60000).catch(() => null),
                fetchJson('/api/leads/board?' + params, 60000).catch(() => null),
            ]);

            state.supabaseConfigured = !!(msgStats && msgStats.configured);
            state.supabaseStats = (msgStats && msgStats.stats) || null;
            state.instantlyConfigured = !!(instantlyData && instantlyData.configured);
            state.instantlyData = instantlyData || null;
            state.leadsBoard = leadsBoard || null;
            // Set summary/performance BEFORE rendering dependent tabs so the
            // Leads reporting strip (acceptance rate, reply rate from HeyReach) populates.
            if (perfData && perfData.summary) {
                state.performance = perfData.performance;
                state.summary = perfData.summary;
            }
            renderEmailTab();
            renderLeadsTab();

            // LinkedIn
            document.getElementById('linkedinLoading').style.display = 'none';
            if (perfData && perfData.summary) {
                const hasData = (perfData.summary.total_connections_sent || 0) > 0;
                if (hasData) {
                    document.getElementById('linkedinContent').style.display = 'block';
                    const r = renderLinkedInTable(perfData.performance, perfData.summary);
                    if (r) renderLinkedInChart(r.dates, r.byDate);
                } else {
                    document.getElementById('linkedinEmpty').style.display = 'block';
                }
            } else {
                document.getElementById('linkedinEmpty').style.display = 'block';
                document.getElementById('linkedinEmpty').querySelector('.empty-text').textContent =
                    (perfData && perfData.error) ? perfData.error : 'Could not load LinkedIn data.';
            }

            // Campaigns
            state.campaigns = (campaignsData && campaignsData.campaigns) ? campaignsData.campaigns : [];
            renderCampaigns();
            renderInsights(state.campaigns);
            renderKpis(state.summary, state.campaigns);
            renderMessageAnalysis(state.summary, state.campaigns);
        } catch (e) {
            console.error('loadAll error', e);
            document.getElementById('linkedinLoading').style.display = 'none';
            document.getElementById('linkedinEmpty').style.display = 'block';
            document.getElementById('linkedinEmpty').querySelector('.empty-text').textContent = 'Request failed: ' + (e.message || e);
            document.getElementById('campaignsLoading').style.display = 'none';
        }

        if (loadBtn) loadBtn.disabled = false;
    }

    // --- Event listeners ---

    if (loadBtn) loadBtn.addEventListener('click', loadAll);

    if (senderSelect) senderSelect.addEventListener('change', e => { state.senderId = e.target.value; });

    document.querySelectorAll('#datePresets button').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#datePresets button').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const days = btn.dataset.days;
            if (days === 'custom') {
                state.rangeDays = 'custom';
                customDates.style.display = 'flex';
                state.startDate = startDateInput.value;
                state.endDate = endDateInput.value;
            } else {
                state.rangeDays = parseInt(days, 10);
                customDates.style.display = 'none';
            }
        });
    });

    if (startDateInput) startDateInput.addEventListener('change', e => state.startDate = e.target.value);
    if (endDateInput) endDateInput.addEventListener('change', e => state.endDate = e.target.value);

    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            // If user hasn't loaded data yet, keep showing the initial state
            const initialEl = document.getElementById('initialState');
            if (initialEl && initialEl.style.display !== 'none') return;
            document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
            const panel = document.getElementById('panel-' + tab.dataset.tab);
            if (panel) panel.classList.add('active');
        });
    });

    const campaignFilterSel = document.getElementById('campaignFilterSelect');
    if (campaignFilterSel) campaignFilterSel.addEventListener('change', e => {
        state.campaignFilter = e.target.value;
        state.campaignsExpanded = false;
        renderCampaigns();
    });

    const sortSel = document.getElementById('campaignSort');
    if (sortSel) sortSel.addEventListener('change', e => { state.campaignSort = e.target.value; renderCampaigns(); });

    // Daily controls
    const dailySortSel = document.getElementById('dailySort');
    if (dailySortSel) dailySortSel.addEventListener('change', e => {
        state.dailySort = e.target.value;
        rerenderDailyOnly();
    });

    const dailyShowMoreBtn = document.getElementById('dailyShowMoreBtn');
    if (dailyShowMoreBtn) dailyShowMoreBtn.addEventListener('click', () => {
        state.dailyExpanded = !state.dailyExpanded;
        rerenderDailyOnly();
    });

    // Campaigns show-more
    const campaignsShowMoreBtn = document.getElementById('campaignsShowMoreBtn');
    if (campaignsShowMoreBtn) campaignsShowMoreBtn.addEventListener('click', () => {
        state.campaignsExpanded = !state.campaignsExpanded;
        renderCampaigns();
    });

    // Leaderboard controls
    const leaderboardOrderSel = document.getElementById('leaderboardOrder');
    if (leaderboardOrderSel) leaderboardOrderSel.addEventListener('change', e => {
        state.leaderboardOrder = e.target.value;
        renderLeaderboard(state.campaigns);
    });

    const leaderboardLimitSel = document.getElementById('leaderboardLimit');
    if (leaderboardLimitSel) leaderboardLimitSel.addEventListener('change', e => {
        state.leaderboardLimit = e.target.value;
        renderLeaderboard(state.campaigns);
    });

    // CSV exports
    const linkedInCsvBtn = document.getElementById('exportLinkedInCsv');
    if (linkedInCsvBtn) linkedInCsvBtn.addEventListener('click', () => {
        if (!state.performance) return;
        const senders = state.performance.senders || {};
        const rows = [['Sender', 'Date', 'Connections Sent', 'Connections Accepted', 'Acceptance Rate %', 'Messages Sent', 'Replies', 'Reply Rate %']];
        Object.entries(senders).forEach(([sender, dailyRows]) => {
            (dailyRows || []).forEach(r => {
                const accRate = (r.connections_sent || 0) > 0 ? (r.connections_accepted / r.connections_sent * 100).toFixed(2) : '0';
                const repRate = (r.messages_sent || 0) > 0 ? (r.message_replies / r.messages_sent * 100).toFixed(2) : '0';
                rows.push([sender, r.date || '', r.connections_sent || 0, r.connections_accepted || 0, accRate, r.messages_sent || 0, r.message_replies || 0, repRate]);
            });
        });
        downloadCsv('linkedin-daily-' + new Date().toISOString().slice(0, 10) + '.csv', rows);
    });

    const campaignCsvBtn = document.getElementById('exportCampaignsCsv');
    if (campaignCsvBtn) campaignCsvBtn.addEventListener('click', () => {
        const rows = [['Campaign', 'Status', 'Sender', 'Sent', 'Accepted', 'Acceptance Rate %', 'Messages', 'Replies', 'Reply Rate %']];
        state.campaigns.forEach(c => {
            rows.push([c.name || '', c.status || '', c.sender || '', c.sent || 0, c.accepted || 0,
                (c.acceptanceRate || 0).toFixed(2), c.messages || 0, c.replies || 0, (c.replyRate || 0).toFixed(2)]);
        });
        downloadCsv('campaigns-' + new Date().toISOString().slice(0, 10) + '.csv', rows);
    });

    // --- Init ---
    setDefaultCustomDates();
    loadSenders();  // Just populate sender dropdown; do NOT auto-load data
})();
