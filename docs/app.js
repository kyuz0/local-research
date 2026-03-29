const statusMsg = document.getElementById('status-message');
const DATASOURCE = 'results.jsonl';

// Global Data
let duckduckgoData = [];

// Initialize
async function init() {
    try {
        const response = await fetch(DATASOURCE);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const text = await response.text();
        parseAndFilterData(text);
        
        if (duckduckgoData.length === 0) {
            statusMsg.textContent = "No valid data found after filtering for DuckDuckGo.";
            statusMsg.classList.add('error');
            return;
        }

        statusMsg.style.display = 'none';
        
        renderProfileCharts();
        renderDynamicCharts();
        renderBM25Charts();
        renderLeaderboard();

    } catch (error) {
        console.error(error);
        statusMsg.textContent = "Failed to load data. Ensure you are running an HTTP server.";
        statusMsg.classList.add('error');
    }
}

function parseAndFilterData(text) {
    const lines = text.split('\n');
    for (let line of lines) {
        if (!line.trim()) continue;
        try {
            const entry = JSON.parse(line);
            if (entry.config && entry.config.search_provider === 'duckduckgo') {
                if (typeof entry.score === 'number' && typeof entry.time_taken_seconds === 'number') {
                    duckduckgoData.push(entry);
                }
            }
        } catch (e) {
            // skip invalid
        }
    }
}

// Math: Group by prompt to average the outcomes of the 3 runs, 
// then average those mins/maxs globally.
function getAggregatedStatsByPrompt(subset) {
    if (subset.length === 0) {
        return { score: { best: 0, avg: 0, worst: 0 }, time: { best: 0, avg: 0, worst: 0 } };
    }
    
    const promptGroups = {};
    for (let d of subset) {
        if (!promptGroups[d.prompt]) {
            promptGroups[d.prompt] = { scores: [], times: [] };
        }
        promptGroups[d.prompt].scores.push(d.score);
        promptGroups[d.prompt].times.push(d.time_taken_seconds);
    }
    
    const localStats = {
        scoreMins: [], scoreMaxs: [], scoreAvgs: [],
        timeMins: [], timeMaxs: [], timeAvgs: []
    };
    
    for (let p in promptGroups) {
        const scores = promptGroups[p].scores;
        const times = promptGroups[p].times;
        
        localStats.scoreMins.push(Math.min(...scores));
        localStats.scoreMaxs.push(Math.max(...scores));
        localStats.scoreAvgs.push(scores.reduce((a,b)=>a+b,0) / scores.length);
        
        localStats.timeMins.push(Math.min(...times));
        localStats.timeMaxs.push(Math.max(...times));
        localStats.timeAvgs.push(times.reduce((a,b)=>a+b,0) / times.length);
    }
    
    const avg = arr => arr.length === 0 ? 0 : arr.reduce((a,b)=>a+b,0)/arr.length;
    
    return {
        score: {
            best: avg(localStats.scoreMaxs),
            avg: avg(localStats.scoreAvgs),
            worst: avg(localStats.scoreMins)
        },
        time: {
            best: avg(localStats.timeMins),
            avg: avg(localStats.timeAvgs),
            worst: avg(localStats.timeMaxs)
        }
    };
}

// Chart Settings
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.color = '#6b7280';
Chart.defaults.plugins.tooltip.padding = 12;
Chart.defaults.plugins.tooltip.cornerRadius = 4;

const chartColors = {
    best: '#10b981',
    avg: '#6366f1',
    worst: '#ef4444'
};

// --- Group 1: Profiles ---
// BASELINE: Dynamic=false, BM25=false
function renderProfileCharts() {
    const profiles = ['shallow', 'default', 'deep'];
    
    const scoreBest = [], scoreAvg = [], scoreWorst = [];
    const timeBest = [], timeAvg = [], timeWorst = [];

    profiles.forEach(p => {
        const subset = duckduckgoData.filter(d => 
            d.config.search_profile === p && 
            d.config.use_dynamic_webpage_analysis === false && 
            d.config.use_bm25_hints === false
        );
        const stats = getAggregatedStatsByPrompt(subset);

        scoreBest.push(stats.score.best);
        scoreAvg.push(stats.score.avg);
        scoreWorst.push(stats.score.worst);

        timeBest.push(stats.time.best);
        timeAvg.push(stats.time.avg);
        timeWorst.push(stats.time.worst);
    });

    const labels = ['Shallow', 'Default', 'Deep'];

    buildBarChart('profilesScoreChart', labels, scoreBest, scoreAvg, scoreWorst, 'Score');
    buildBarChart('profilesTimeChart', labels, timeBest, timeAvg, timeWorst, 'Seconds');
}

// --- Group 2: Dynamic Analysis ---
// BASELINE: Profile=default, BM25=false
function renderDynamicCharts() {
    const states = [true, false];
    
    const scoreBest = [], scoreAvg = [], scoreWorst = [];
    const timeBest = [], timeAvg = [], timeWorst = [];

    states.forEach(state => {
        const subset = duckduckgoData.filter(d => 
            d.config.use_dynamic_webpage_analysis === state &&
            d.config.search_profile === 'default' &&
            d.config.use_bm25_hints === false
        );
        const stats = getAggregatedStatsByPrompt(subset);

        scoreBest.push(stats.score.best);
        scoreAvg.push(stats.score.avg);
        scoreWorst.push(stats.score.worst);

        timeBest.push(stats.time.best);
        timeAvg.push(stats.time.avg);
        timeWorst.push(stats.time.worst);
    });

    const labels = ['Enabled (True)', 'Disabled (False)'];
    
    buildBarChart('dynScoreChart', labels, scoreBest, scoreAvg, scoreWorst, 'Score');
    buildBarChart('dynTimeChart', labels, timeBest, timeAvg, timeWorst, 'Seconds');
}

// --- Group 3: BM25 Hints ---
// BASELINE: Profile=default, Dynamic=true
function renderBM25Charts() {
    const states = [true, false];
    
    const scoreBest = [], scoreAvg = [], scoreWorst = [];
    const timeBest = [], timeAvg = [], timeWorst = [];

    states.forEach(state => {
        const subset = duckduckgoData.filter(d => 
            d.config.use_bm25_hints === state &&
            d.config.search_profile === 'default' &&
            d.config.use_dynamic_webpage_analysis === true
        );
        const stats = getAggregatedStatsByPrompt(subset);

        scoreBest.push(stats.score.best);
        scoreAvg.push(stats.score.avg);
        scoreWorst.push(stats.score.worst);

        timeBest.push(stats.time.best);
        timeAvg.push(stats.time.avg);
        timeWorst.push(stats.time.worst);
    });

    const labels = ['Enabled (True)', 'Disabled (False)'];
    
    buildBarChart('bm25ScoreChart', labels, scoreBest, scoreAvg, scoreWorst, 'Score');
    buildBarChart('bm25TimeChart', labels, timeBest, timeAvg, timeWorst, 'Seconds');
}

function buildBarChart(canvasId, labels, bestData, avgData, worstData, yLabel) {
    const ctx = document.getElementById(canvasId).getContext('2d');
    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Best',
                    data: bestData,
                    backgroundColor: chartColors.best,
                    borderRadius: 4
                },
                {
                    label: 'Average',
                    data: avgData,
                    backgroundColor: chartColors.avg,
                    borderRadius: 4
                },
                {
                    label: 'Worst',
                    data: worstData,
                    backgroundColor: chartColors.worst,
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    title: { display: true, text: yLabel },
                    grid: { borderDash: [4, 4], color: '#e5e7eb' }
                },
                x: {
                    grid: { display: false }
                }
            },
            plugins: {
                legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 8 } }
            }
        }
    });
}

// --- LEADERBOARD LOGIC ---
function renderLeaderboard() {
    const variantsMap = {};

    for(let d of duckduckgoData) {
        // Create unique key for the variant
        const key = `${d.config.search_profile}|${d.config.use_dynamic_webpage_analysis}|${d.config.use_bm25_hints}`;
        if(!variantsMap[key]) variantsMap[key] = [];
        variantsMap[key].push(d);
    }

    const compiledVariants = [];

    for (const [key, subset] of Object.entries(variantsMap)) {
        const stats = getAggregatedStatsByPrompt(subset);
        const [profile, dynamicStr, bm25Str] = key.split('|');
        compiledVariants.push({
            profile,
            dynamic: dynamicStr === 'true',
            bm25: bm25Str === 'true',
            stats: stats
        });
    }

    // Sort by Best Average Score -> Best Average Time
    compiledVariants.sort((a, b) => {
        if(b.stats.score.avg !== a.stats.score.avg) {
            return b.stats.score.avg - a.stats.score.avg; // Descending
        }
        return a.stats.time.avg - b.stats.time.avg; // Ascending time
    });

    const tbody = document.querySelector('#leaderboard-table tbody');
    tbody.innerHTML = '';

    compiledVariants.forEach((v, index) => {
        const tr = document.createElement('tr');
        
        const dynBadge = v.dynamic ? 'badge true' : 'badge false';
        const bmBadge = v.bm25 ? 'badge true' : 'badge false';

        tr.innerHTML = `
            <td><div class="rank-pill">${index + 1}</div></td>
            <td style="text-transform: capitalize;" class="val-focus">${v.profile}</td>
            <td><span class="${dynBadge}">${v.dynamic ? 'ON' : 'OFF'}</span></td>
            <td><span class="${bmBadge}">${v.bm25 ? 'ON' : 'OFF'}</span></td>
            <td class="val-focus">${v.stats.score.avg.toFixed(2)}</td>
            <td>${v.stats.time.avg.toFixed(1)}s</td>
            <td style="color:var(--success)">${v.stats.score.best.toFixed(2)}</td>
            <td style="color:var(--danger)">${v.stats.score.worst.toFixed(2)}</td>
            <td style="color:var(--success)">${v.stats.time.best.toFixed(1)}s</td>
            <td style="color:var(--danger)">${v.stats.time.worst.toFixed(1)}s</td>
        `;
        tbody.appendChild(tr);
    });
}

window.addEventListener('DOMContentLoaded', init);
