const palette = {
  purple: '#7567F8', blue: '#4098F7', teal: '#22B8A7', orange: '#F3A34D', pink: '#E879A9',
  navy: '#243B64', green: '#33A879', red: '#E66E6E', lavender: '#AA9BFF', gray: '#9AA7BA'
};
const chartColors = [palette.purple, palette.blue, palette.teal, palette.orange, palette.pink, palette.green, palette.navy, palette.lavender, palette.red, palette.gray];
let opportunityRows = [];
let opportunityColumns = [];
let genericRecordMode = false;
let genericSortConfigured = false;
let chartSelections = {};

function compactINR(value) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  const abs = Math.abs(n);
  if (abs >= 1e7) return `₹${(n / 1e7).toFixed(1).replace(/\.0$/, '')} Cr`;
  if (abs >= 1e5) return `₹${(n / 1e5).toFixed(1).replace(/\.0$/, '')} L`;
  if (abs >= 1e3) return `₹${(n / 1e3).toFixed(1).replace(/\.0$/, '')} K`;
  return `₹${Math.round(n).toLocaleString('en-IN')}`;
}
function percent(value) {
  if (value === null || value === undefined || value === '') return '—';
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(1)}%` : '—';
}
function safeText(value) { return value === null || value === undefined ? '' : String(value); }
function showState(message, kind = 'loading') {
  const el = document.getElementById('loadState');
  if (!el) return;
  el.className = `load-state ${kind}`;
  el.textContent = message;
  el.hidden = !message;
}
function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}
function filterQuery() {
  const params = new URLSearchParams();
  const fields = {
    start_date: 'startDate', end_date: 'endDate', Region: 'regionFilter',
    Salesperson: 'personFilter', Product: 'productFilter', Industry: 'industryFilter', Lead_Source: 'sourceFilter'
  };
  Object.entries(fields).forEach(([key, id]) => {
    const el = document.getElementById(id);
    if (el && el.value && el.value !== 'All') params.set(key, el.value);
  });
  const genericFilters = {};
  const genericRanges = {};
  Object.entries(chartSelections).forEach(([key, value]) => {
    if (value === null || value === undefined || value === '') return;
    if (key.startsWith('generic-range:')) genericRanges[key.slice(14)] = value;
    else if (key.startsWith('generic:')) genericFilters[key.slice(8)] = value;
    else params.set(key, value);
  });
  if (Object.keys(genericFilters).length) params.set('gfilter', JSON.stringify(genericFilters));
  if (Object.keys(genericRanges).length) params.set('grange', JSON.stringify(genericRanges));
  return params.toString();
}
async function fetchJSON(url) {
  const response = await fetch(url, { headers: { Accept: 'application/json' } });
  let payload;
  try { payload = await response.json(); }
  catch (_error) { throw new Error('The server returned an unreadable response.'); }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  if (payload.error) throw new Error(payload.error);
  return payload;
}

function updateSelectionBanner() {
  const wrap = document.getElementById('interactiveSelection');
  const text = document.getElementById('interactiveSelectionText');
  const parts = Object.entries(chartSelections).map(([key, value]) => `${key.replace('_', ' ')}: ${value}`);
  if (wrap) wrap.hidden = parts.length === 0;
  if (text) text.textContent = parts.length ? `Chart cross-filter active · ${parts.join(' · ')}` : '';
}
function refreshCurrentView() {
  updateSelectionBanner();
  if (document.getElementById('loadState')) loadAnalytics();
  if (document.getElementById('opportunityBody')) loadOpportunities();
}
function applyChartSelection(field, value) {
  if (!field || value === null || value === undefined || value === '') return;
  // Keep chart clicks additive so users can combine a region and a product.
  chartSelections[field] = String(value);
  if (field.startsWith('generic:') || field.startsWith('generic-range:')) {
    refreshCurrentView();
    return;
  }
  const controlIds = { Region: 'regionFilter', Salesperson: 'personFilter', Product: 'productFilter', Industry: 'industryFilter', Lead_Source: 'sourceFilter' };
  const control = document.getElementById(controlIds[field]);
  if (control && Array.from(control.options).some(option => option.value === String(value))) control.value = String(value);
  refreshCurrentView();
}
function svgEl(name, attrs = {}) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}
function svgText(svg, x, y, value, anchor = 'start', className = 'chart-axis-label') {
  const node = svgEl('text', { x, y, 'text-anchor': anchor, class: className });
  node.textContent = String(value);
  svg.append(node);
  return node;
}
function chartTooltip(label, value, currency) {
  return `${label}: ${currency ? compactINR(value) : Number(value).toLocaleString('en-IN')}`;
}
function drawChart(id, config, filterField = null, currency = true) {
  const container = document.getElementById(id);
  if (!container) return;
  container.replaceChildren();
  const labels = config.data?.labels || [];
  const dataset = config.data?.datasets?.[0] || {};
  const values = (dataset.data || []).map(value => Number(value) || 0);
  const type = config.type || 'bar';
  if (!labels.length || !values.some(value => value !== 0)) {
    const note = document.createElement('div');
    note.className = 'chart-empty';
    note.textContent = 'No data available for this visualization with the current dataset and filters.';
    container.append(note);
    return;
  }

  const width = 900;
  const height = 270;
  const svg = svgEl('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `${dataset.label || 'Sales'} chart`, class: 'custom-chart', preserveAspectRatio: 'xMidYMid meet' });
  const colors = dataset.backgroundColor || chartColors;
  const clickable = (element, index) => {
    if (filterField) {
      element.classList.add('chart-mark');
      element.setAttribute('tabindex', '0');
      element.setAttribute('role', 'button');
      element.setAttribute('aria-label', `Filter by ${filterField}: ${labels[index]}`);
      const selectionValue = dataset.filterValues?.[index] ?? labels[index];
      const activate = () => applyChartSelection(filterField, selectionValue);
      element.addEventListener('click', activate);
      element.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); } });
    }
  };

  if (type === 'doughnut') {
    const cx = 440, cy = 126, radius = 82, stroke = 34;
    const total = values.reduce((a, b) => a + Math.max(0, b), 0) || 1;
    const circumference = 2 * Math.PI * radius;
    let offset = 0;
    values.forEach((value, index) => {
      const segment = Math.max(0, value) / total * circumference;
      const circle = svgEl('circle', {
        cx, cy, r: radius, fill: 'none', stroke: colors[index % colors.length],
        'stroke-width': stroke, 'stroke-dasharray': `${segment} ${circumference - segment}`,
        'stroke-dashoffset': -offset, transform: `rotate(-90 ${cx} ${cy})`,
        class: 'chart-doughnut-segment'
      });
      const title = svgEl('title'); title.textContent = chartTooltip(labels[index], value, currency); circle.append(title);
      clickable(circle, index);
      svg.append(circle);
      offset += segment;
    });
    svgText(svg, cx, cy - 3, currency ? compactINR(total) : total.toLocaleString('en-IN'), 'middle', 'chart-center-value');
    svgText(svg, cx, cy + 18, dataset.label || (currency ? 'Revenue' : 'Records'), 'middle', 'chart-center-label');
    const legend = document.createElement('div'); legend.className = 'chart-legend';
    labels.forEach((label, index) => {
      const item = document.createElement('button'); item.type = 'button'; item.className = 'chart-legend-item';
      const swatch = document.createElement('span'); swatch.className = 'chart-legend-swatch'; swatch.style.backgroundColor = colors[index % colors.length];
      const name = document.createElement('span'); name.textContent = label;
      const amount = document.createElement('b'); amount.textContent = currency ? compactINR(values[index]) : values[index].toLocaleString('en-IN');
      item.append(swatch, name, amount);
      item.addEventListener('click', () => filterField && applyChartSelection(filterField, label));
      legend.append(item);
    });
    container.append(svg, legend);
    return;
  }

  const left = 142, right = 34, top = 15, bottom = 30;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const max = Math.max(...values, 1) * 1.12;
  const min = Math.min(...values, 0) * 1.12;
  const formatTick = value => currency ? compactINR(value) : Number(value).toLocaleString('en-IN');

  if (type === 'line') {
    const domain = Math.max(1, max - min);
    for (let tick = 0; tick <= 4; tick++) {
      const value = min + domain * tick / 4;
      const y = top + plotH - (tick / 4) * plotH;
      svg.append(svgEl('line', { x1: left, y1: y, x2: width - right, y2: y, class: 'chart-grid-line' }));
      svgText(svg, left - 10, y + 4, formatTick(value), 'end');
    }
    const points = values.map((value, index) => ({
      x: labels.length === 1 ? left + plotW / 2 : left + (index / (labels.length - 1)) * plotW,
      y: top + plotH - ((value - min) / domain) * plotH,
      value, index
    }));
    const path = points.map((point, index) => `${index ? 'L' : 'M'} ${point.x} ${point.y}`).join(' ');
    svg.append(svgEl('path', { d: path, class: 'chart-line-path' }));
    points.forEach(point => {
      const circle = svgEl('circle', { cx: point.x, cy: point.y, r: 6, fill: palette.purple, class: 'chart-line-point' });
      const title = svgEl('title'); title.textContent = chartTooltip(labels[point.index], point.value, currency); circle.append(title);
      clickable(circle, point.index); svg.append(circle);
      const every = Math.max(1, Math.ceil(labels.length / 9));
      if (point.index % every === 0 || point.index === labels.length - 1) {
        svgText(svg, point.x, height - 8, labels[point.index], 'middle', 'chart-x-label');
      }
    });
  } else {
    const rowH = Math.min(31, plotH / Math.max(values.length, 1));
    for (let tick = 0; tick <= 4; tick++) {
      const value = max * tick / 4;
      const x = left + (tick / 4) * plotW;
      svg.append(svgEl('line', { x1: x, y1: top, x2: x, y2: height - bottom, class: 'chart-grid-line' }));
      svgText(svg, x, height - 8, formatTick(value), 'middle', 'chart-x-label');
    }
    values.forEach((value, index) => {
      const y = top + index * (plotH / values.length) + (plotH / values.length - rowH) / 2;
      svgText(svg, left - 12, y + rowH * .72, labels[index].length > 22 ? `${labels[index].slice(0, 20)}…` : labels[index], 'end', 'chart-y-label');
      const barWidth = Math.max(0, (value / max) * plotW);
      const rect = svgEl('rect', { x: left, y, width: Math.max(barWidth, value > 0 ? 2 : 0), height: rowH, rx: 6, fill: colors[index % colors.length], class: 'chart-bar' });
      const title = svgEl('title'); title.textContent = chartTooltip(labels[index], value, currency); rect.append(title);
      clickable(rect, index); svg.append(rect);
    });
  }
  container.append(svg);
}
function groupedChart(id, data, type = 'bar', horizontal = false, filterField = null, currency = true) {
  const labels = data?.labels || [];
  const values = data?.values || [];
  const colors = values.map((_, i) => chartColors[i % chartColors.length]);
  const metricLabel = data?.label || (currency ? 'Revenue' : 'Records');
  drawChart(id, {
    type,
    data: { labels, datasets: [{ label: metricLabel, data: values, backgroundColor: colors, filterValues: data?.filter_values || [] }] },
    options: { indexAxis: horizontal ? 'y' : 'x' }
  }, filterField, currency);
}

function renderKPIs(k) {
  const moneyMap = { kpiRevenue: 'total_revenue', kpiPipeline: 'pipeline_value', kpiExpected: 'expected_revenue', kpiAverage: 'avg_deal' };
  Object.entries(moneyMap).forEach(([id, key]) => setText(id, compactINR(k[key])));
  const percentMap = { kpiWinRate: 'win_rate', kpiLossRate: 'loss_rate', kpiConversion: 'conversion_rate' };
  Object.entries(percentMap).forEach(([id, key]) => setText(id, percent(k[key])));
  setText('kpiOpen', Number(k.open_deals || 0).toLocaleString('en-IN'));
  setText('kpiLost', Number(k.lost_deals || 0).toLocaleString('en-IN'));
  setText('kpiWon', Number(k.won_deals || 0).toLocaleString('en-IN'));
  setText('kpiTotal', Number(k.opportunities || 0).toLocaleString('en-IN'));
  setText('kpiCycle', k.avg_cycle === null ? '—' : `${Number(k.avg_cycle).toFixed(0)} days`);
  setText('kpiOverdue', Number(k.overdue_deals || 0).toLocaleString('en-IN'));
  setText('kpiOverdueValue', compactINR(k.overdue_pipeline_value));
  setText('kpiConversion', percent(k.conversion_rate));
  if (k.has_pipeline) {
    setText('labelOpen', 'Open deals'); setText('labelLost', 'Lost deals'); setText('labelWon', 'Won deals');
    setText('labelAverage', 'Average deal size'); setText('labelTotal', 'Total opportunities');
    setText('kpiOpen', Number(k.open_deals || 0).toLocaleString('en-IN'));
    setText('kpiLost', Number(k.lost_deals || 0).toLocaleString('en-IN'));
    setText('kpiWon', Number(k.won_deals || 0).toLocaleString('en-IN'));
  } else {
    setText('labelOpen', 'Open opportunities'); setText('labelLost', 'Lost opportunities'); setText('labelWon', 'Won deals');
    setText('labelAverage', 'Average sale size'); setText('labelTotal', 'Sales records');
    setText('kpiOpen', '—'); setText('kpiLost', '—'); setText('kpiWon', '—');
  }
}
function renderFunnel(stage, hasPipeline) {
  const el = document.getElementById('pipelineFunnel');
  if (!el) return;
  el.replaceChildren();
  if (!hasPipeline) {
    const message = document.createElement('div');
    message.className = 'chart-empty';
    message.textContent = 'This is a transaction sales dataset. Upload a CRM opportunities file with a Stage or Probability field to activate the pipeline funnel.';
    el.append(message);
    return;
  }
  // Use every detected open stage, including custom stages such as Prospecting or Demo.
  const labels = stage.labels || [];
  const values = labels.map((label, i) => [label, Number(stage.values?.[i] || 0)]);
  if (!values.length) {
    const note = document.createElement('div'); note.className = 'chart-empty';
    note.textContent = 'No open opportunities match the selected filters.'; el.append(note); return;
  }
  const max = Math.max(...values.map(x => x[1]), 1);
  values.forEach(([label, val], i) => {
    const row = document.createElement('div'); row.className = 'funnel-stage'; row.tabIndex = 0; row.setAttribute('role', 'button'); row.setAttribute('aria-label', `Filter by ${label}`);
    const info = document.createElement('div'); info.className = 'funnel-info';
    const name = document.createElement('b'); name.textContent = `${i + 1}. ${label}`;
    const value = document.createElement('strong'); value.textContent = compactINR(val);
    info.append(name, value);
    const track = document.createElement('div'); track.className = 'funnel-track';
    const bar = document.createElement('div'); bar.className = `funnel-bar funnel-${i + 1}`; bar.style.backgroundColor = chartColors[i % chartColors.length]; bar.style.width = `${Math.max(val / max * 100, val ? 2 : 0)}%`;
    track.append(bar); row.append(info, track);
    row.addEventListener('click', () => applyChartSelection('Stage', label));
    row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); applyChartSelection('Stage', label); } });
    el.append(row);
  });
}
function renderRegions(data) {
  const container = document.getElementById('regionCards');
  if (!container) return;
  container.replaceChildren();
  (data.labels || []).forEach((label, i) => {
    const card = document.createElement('article'); card.className = 'region-card'; card.tabIndex = 0; card.setAttribute('role', 'button'); card.setAttribute('aria-label', `Filter by region ${label}`);
    const title = document.createElement('span'); title.textContent = label;
    const value = document.createElement('b'); value.textContent = compactINR(data.values[i]);
    card.append(title, value);
    card.addEventListener('click', () => applyChartSelection('Region', label));
    card.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); applyChartSelection('Region', label); } });
    container.append(card);
  });
}
function renderDatasetNotice(dataset) {
  const node = document.getElementById('datasetNotice');
  if (!node || !dataset) return;
  node.textContent = `${dataset.data_type || 'Sales dataset'} · ${dataset.message || ''}`;
  node.classList.add('visible');
}
function appendGenericKpi(container, label, value, detail = '') {
  const card = document.createElement('article'); card.className = 'kpi-card generic-kpi-card';
  const title = document.createElement('span'); title.textContent = label;
  const strong = document.createElement('strong'); strong.textContent = value;
  const small = document.createElement('small'); small.textContent = detail;
  card.append(title, strong, small); container.append(card);
}
function renderGenericInsights(data) {
  const workspace = document.getElementById('genericInsights');
  if (!workspace || !data) return;
  workspace.hidden = false;
  const kpis = document.getElementById('genericKpis'); kpis.replaceChildren();
  appendGenericKpi(kpis, 'Rows analyzed', Number(data.row_count || 0).toLocaleString('en-IN'), 'Records in the current filtered view');
  appendGenericKpi(kpis, 'Columns detected', Number(data.column_count || 0).toLocaleString('en-IN'), 'All uploaded fields retained');
  appendGenericKpi(kpis, 'Numeric fields', Number((data.numeric_columns || []).length).toLocaleString('en-IN'), 'Available for numeric summaries');
  appendGenericKpi(kpis, 'Data completeness', `${Number(data.quality?.completeness_pct ?? 100).toFixed(1)}%`, 'Non-empty cells across the dataset');

  const insightList = document.getElementById('genericInsightList'); insightList.replaceChildren();
  (data.insights || []).forEach(text => { const li = document.createElement('li'); li.textContent = text; insightList.append(li); });
  if (!insightList.children.length) { const li = document.createElement('li'); li.textContent = 'Not enough non-empty values were found to generate additional findings.'; insightList.append(li); }

  const quality = document.getElementById('genericQuality'); quality.replaceChildren();
  const qualityItems = [
    ['Blank cells', Number(data.quality?.missing_cells || 0).toLocaleString('en-IN')],
    ['Duplicate rows', Number(data.quality?.duplicate_rows || 0).toLocaleString('en-IN')],
    ['Filled cells', `${Number(data.quality?.completeness_pct ?? 100).toFixed(1)}%`]
  ];
  qualityItems.forEach(([label, value]) => { const item=document.createElement('div'); item.className='quality-item'; const b=document.createElement('b'); b.textContent=value; const span=document.createElement('span'); span.textContent=label; item.append(b,span); quality.append(item); });

  const trend = data.trend || {};
  const trendTitle = document.getElementById('genericTrendTitle');
  const trendSubtitle = document.getElementById('genericTrendSubtitle');
  if (trendTitle) trendTitle.textContent = trend.date_field ? `Monthly trend · ${trend.date_field}` : 'Time trend unavailable';
  if (trendSubtitle) trendSubtitle.textContent = trend.metric_field ? `${trend.aggregation} of ${trend.metric_field} by month. Click a point to filter.` : (trend.date_field ? 'Record count by month. Click a point to filter.' : 'No date-like column was detected in this dataset.');
  groupedChart('genericTrendChart', {labels:trend.labels || [], values:trend.values || [], label:trend.metric_field || 'Records'}, 'line', false, trend.date_field ? 'month' : null, false);

  const categoryContainer = document.getElementById('genericCategoryCharts'); categoryContainer.replaceChildren();
  (data.category_charts || []).forEach((chart, index) => {
    const card=document.createElement('article'); card.className='generic-chart-card';
    const h=document.createElement('h3'); h.textContent=chart.field;
    const p=document.createElement('p'); p.textContent=chart.metric === 'Record count' ? 'Record count by category. Click a bar to filter.' : `${chart.aggregation} of ${chart.metric} by category. Click a bar to filter.`;
    const chartArea=document.createElement('div'); chartArea.className='chart-area';
    const canvas=document.createElement('div'); canvas.className='chart-canvas'; canvas.id=`genericCategoryChart${index}`;
    chartArea.append(canvas); card.append(h,p,chartArea); categoryContainer.append(card);
    groupedChart(canvas.id, {labels:chart.labels || [], values:chart.values || [], label:chart.metric || 'Records'}, 'bar', true, `generic:${chart.field}`, false);
  });
  if (!categoryContainer.children.length) {
    const note=document.createElement('div'); note.className='chart-empty'; note.textContent='No low-cardinality category fields were detected. Review the numeric summaries and column profile below.'; categoryContainer.append(note);
  }

  (data.numeric_histograms || []).forEach((histogram, index) => {
    const card=document.createElement('article'); card.className='generic-chart-card';
    const h=document.createElement('h3'); h.textContent=`${histogram.field} · distribution`;
    const p=document.createElement('p'); p.textContent='Number of rows in each numeric range. Click a bar to filter the dataset to that range.';
    const chartArea=document.createElement('div'); chartArea.className='chart-area';
    const canvas=document.createElement('div'); canvas.className='chart-canvas'; canvas.id=`genericHistogram${index}`;
    chartArea.append(canvas); card.append(h,p,chartArea); categoryContainer.append(card);
    const filterValues=(histogram.ranges || []).map(range=>`${range[0]}|${range[1]}`);
    groupedChart(canvas.id,{labels:histogram.labels || [],values:histogram.values || [],label:'Records',filter_values:filterValues},'bar',true,`generic-range:${histogram.field}`,false);
  });

  const numericBody=document.getElementById('genericNumericBody'); numericBody.replaceChildren();
  (data.numeric_summary || []).forEach(item => {
    const tr=document.createElement('tr');
    [item.field, Number(item.count).toLocaleString('en-IN'), item.sum, item.mean, item.median, item.min, item.max].forEach((value,index)=>{
      const td=document.createElement('td'); td.textContent=index===0?String(value):Number(value).toLocaleString('en-IN',{maximumFractionDigits:2}); tr.append(td);
    }); numericBody.append(tr);
  });
  if (!numericBody.children.length) { const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=7; td.textContent='No numeric columns detected. This is acceptable: category and text profiling is still available.'; tr.append(td); numericBody.append(tr); }

  const profileBody=document.getElementById('genericProfileBody'); profileBody.replaceChildren();
  (data.profiles || []).forEach(profile => {
    const tr=document.createElement('tr');
    [profile.name,profile.type,profile.non_empty,profile.missing,profile.distinct].forEach(value=>{const td=document.createElement('td'); td.textContent=String(value); tr.append(td);});
    profileBody.append(tr);
  });
  const hint=document.getElementById('genericSelectionHint');
  if (hint) { const active=Object.entries(chartSelections).filter(([key])=>key.startsWith('generic:') || key.startsWith('generic-range:') || key==='month').map(([key,value])=>`${key.startsWith('generic-range:')?key.slice(14):(key.startsWith('generic:')?key.slice(8):'Month')}: ${value}`); hint.textContent=active.length?`Active cross-filter · ${active.join(' · ')}`:'All records are included.'; }
}

async function loadAnalytics() {
  showState('Loading analytics…', 'loading');
  try {
    const payload = await fetchJSON(`/api/analytics?${filterQuery()}`);
    renderDatasetNotice(payload.dataset);
    const genericMode = payload.dataset?.data_type === 'Exploratory dataset';
    const salesContent = document.getElementById('salesDashboardContent');
    const genericWorkspace = document.getElementById('genericInsights');
    if (genericMode) {
      if (salesContent) salesContent.hidden = true;
      if (genericWorkspace) {
        genericWorkspace.hidden = false;
        renderGenericInsights(payload.generic || {});
        showState(`Automatic analysis ready · ${Number(payload.generic?.row_count || 0).toLocaleString('en-IN')} rows · ${Number(payload.generic?.column_count || 0).toLocaleString('en-IN')} columns.`, 'success');
      } else {
        showState('This file has no predefined sales schema. Open Overview to explore the uploaded columns and generated insights.', 'success');
      }
      return;
    }
    if (salesContent) salesContent.hidden = false;
    if (genericWorkspace) genericWorkspace.hidden = true;
    renderKPIs(payload.kpis);
    if (document.getElementById('monthlyChart')) groupedChart('monthlyChart', payload.monthly, 'line', false, 'month', true);
    if (document.getElementById('outcomesChart')) groupedChart('outcomesChart', payload.status_counts, 'doughnut', false, payload.kpis.has_pipeline ? 'Status' : null, false);
    if (document.getElementById('productChart')) groupedChart('productChart', payload.product, 'bar', true, 'Product', true);
    if (document.getElementById('sourceChart')) groupedChart('sourceChart', payload.lead_source, 'bar', true, 'Lead_Source', true);
    if (document.getElementById('stageChart')) groupedChart('stageChart', payload.stage, 'bar', true, 'Stage', true);
    if (document.getElementById('regionPipelineChart')) groupedChart('regionPipelineChart', payload.pipeline_region, 'bar', true, 'Region', true);
    if (document.getElementById('regionChart')) groupedChart('regionChart', payload.region, 'doughnut', false, 'Region', true);
    if (document.getElementById('industryChart')) groupedChart('industryChart', payload.industry, 'bar', true, 'Industry', true);
    if (document.getElementById('salespersonChart')) groupedChart('salespersonChart', payload.salesperson, 'bar', true, 'Salesperson', true);
    renderFunnel(payload.stage, payload.kpis.has_pipeline);
    renderRegions(payload.region);
    showState(`Analytics loaded · ${Number(payload.kpis.opportunities).toLocaleString('en-IN')} records in this view. Click a chart bar, slice or funnel stage to cross-filter.`, 'success');
  } catch (error) {
    showState(`Unable to load analytics. ${error.message}`, 'error');
  }
}
function renderOpportunityTable(rows) {
  const body = document.getElementById('opportunityBody');
  if (!body) return;
  body.replaceChildren();
  const head = document.getElementById('opportunityHead');
  if (head) {
    head.replaceChildren();
    opportunityColumns.forEach(field => { const th=document.createElement('th'); th.textContent=field; head.append(th); });
  }
  rows.forEach(row => {
    const tr = document.createElement('tr');
    opportunityColumns.forEach(field => {
      const td = document.createElement('td');
      const value = row[field];
      if (!genericRecordMode && ['Deal_Value', 'Expected_Revenue', 'Revenue'].includes(field)) td.textContent = compactINR(value);
      else if (!genericRecordMode && field === 'Probability') td.textContent = value === null || value === undefined || value === '' ? '—' : percent(Number(value) * 100);
      else if (!genericRecordMode && field === 'Is_Overdue') td.textContent = value ? 'Yes' : 'No';
      else if (!genericRecordMode && field === 'Status') {
        const badge = document.createElement('span');
        badge.className = `status-badge status-${safeText(value).toLowerCase()}`;
        badge.textContent = safeText(value); td.append(badge);
      } else td.textContent = safeText(value);
      tr.append(td);
    });
    body.append(tr);
  });
  setText('opportunityCount', `${rows.length.toLocaleString('en-IN')} matching rows`);
  setText('tableFeedback', rows.length ? '' : 'No records match these filters.');
}

async function loadOpportunities() {
  setText('tableFeedback', 'Loading sales records…');
  try {
    const sortSelect = document.getElementById('tableSort');
    const sortVal = sortSelect?.value || 'expected_revenue:desc';
    const splitAt = sortVal.lastIndexOf(':');
    const sort = splitAt >= 0 ? sortVal.slice(0, splitAt) : sortVal;
    const order = splitAt >= 0 ? sortVal.slice(splitAt + 1) : 'desc';
    const qs = new URLSearchParams(filterQuery()); qs.set('sort', sort); qs.set('order', order); qs.set('q', document.getElementById('tableSearch')?.value || '');
    if (genericSortConfigured && genericRecordMode) { qs.set('sort_field', decodeURIComponent(sort)); qs.set('sort_order', order); }
    const payload = await fetchJSON(`/api/opportunities?${qs.toString()}`);
    opportunityRows = payload.rows || [];
    genericRecordMode = Boolean(payload.generic);
    opportunityColumns = payload.columns || ['Deal_ID', 'Salesperson', 'Company', 'Product', 'Industry', 'Lead_Source', 'Region', 'Stage', 'Deal_Value', 'Probability', 'Expected_Revenue', 'Expected_Close_Date', 'Status', 'Is_Overdue'];
    if (payload.generic && sortSelect && !genericSortConfigured) {
      sortSelect.replaceChildren();
      opportunityColumns.forEach(field => {
        const desc = document.createElement('option'); desc.value = `${encodeURIComponent(field)}:desc`; desc.textContent = `${field} · descending`; sortSelect.append(desc);
        const asc = document.createElement('option'); asc.value = `${encodeURIComponent(field)}:asc`; asc.textContent = `${field} · ascending`; sortSelect.append(asc);
      });
      genericSortConfigured = true;
      if (opportunityColumns.length) { sortSelect.value = `${encodeURIComponent(opportunityColumns[0])}:desc`; return loadOpportunities(); }
    }
    renderOpportunityTable(opportunityRows);
  } catch (error) {
    setText('tableFeedback', `Unable to load sales records. ${error.message}`);
  }
}
function exportVisibleCSV() {
  const fields = opportunityColumns.length ? opportunityColumns : ['Deal_ID', 'Salesperson', 'Company', 'Product', 'Industry', 'Lead_Source', 'Region', 'Stage', 'Deal_Value', 'Probability', 'Expected_Revenue', 'Expected_Close_Date', 'Status', 'Revenue', 'Quantity', 'Sales_Cycle_Days', 'Is_Overdue'];
  const quote = value => `"${safeText(value).replace(/"/g, '""')}"`;
  const csv = [fields.map(quote).join(','), ...opportunityRows.map(row => fields.map(field => quote(row[field] ?? '')).join(','))].join('\r\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'sales_opportunities_export.csv'; anchor.click();
  URL.revokeObjectURL(url);
}

function initPage() {
  const hasAnalytics = document.getElementById('loadState');
  if (hasAnalytics) loadAnalytics();
  if (document.getElementById('opportunityBody')) loadOpportunities();
  const filterIds = ['startDate', 'endDate', 'regionFilter', 'personFilter', 'productFilter', 'industryFilter', 'sourceFilter'];
  filterIds.forEach(id => document.getElementById(id)?.addEventListener('change', () => {
    // A manual filter takes precedence over any chart selection for the same field.
    const fieldForId = { regionFilter: 'Region', personFilter: 'Salesperson', productFilter: 'Product', industryFilter: 'Industry', sourceFilter: 'Lead_Source' }[id];
    if (fieldForId) delete chartSelections[fieldForId];
    if (id === 'startDate' || id === 'endDate') delete chartSelections.month;
    refreshCurrentView();
  }));
  document.getElementById('resetFilters')?.addEventListener('click', () => {
    filterIds.forEach(id => { const element = document.getElementById(id); if (element) element.value = element.tagName === 'SELECT' ? 'All' : ''; });
    chartSelections = {};
    refreshCurrentView();
  });
  document.getElementById('clearChartSelection')?.addEventListener('click', () => {
    chartSelections = {};
    ['regionFilter', 'personFilter', 'productFilter', 'industryFilter', 'sourceFilter'].forEach(id => {
      const element = document.getElementById(id); if (element) element.value = 'All';
    });
    refreshCurrentView();
  });
  let searchTimer = null;
  document.getElementById('tableSearch')?.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadOpportunities, 220); });
  document.getElementById('tableSort')?.addEventListener('change', loadOpportunities);
  document.getElementById('downloadCsv')?.addEventListener('click', exportVisibleCSV);
  document.getElementById('csvFile')?.addEventListener('change', event => setText('fileName', event.target.files?.[0]?.name || 'No file selected'));
  document.getElementById('uploadForm')?.addEventListener('submit', event => {
    const input = document.getElementById('csvFile');
    if (!input?.files?.length) { event.preventDefault(); setText('fileName', 'Select a CSV file first.'); }
    else if (!input.files[0].name.toLowerCase().endsWith('.csv')) { event.preventDefault(); setText('fileName', 'Please upload a CSV file.'); }
  });
  updateSelectionBanner();
}
document.addEventListener('DOMContentLoaded', initPage);
