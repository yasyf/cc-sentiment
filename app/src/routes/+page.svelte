<script lang="ts">
	import SentimentTimeline from '$lib/charts/SentimentTimeline.svelte';
	import HourlyHeatmap from '$lib/charts/HourlyHeatmap.svelte';
	import WeekdayBar from '$lib/charts/WeekdayBar.svelte';
	import DistributionHistogram from '$lib/charts/DistributionHistogram.svelte';
	import type { PageData } from './$types.js';

	let { data }: { data: PageData } = $props();

	const lastUpdated = $derived(
		new Date(data.last_updated).toLocaleString('en-US', {
			month: 'short',
			day: 'numeric',
			year: 'numeric',
			hour: 'numeric',
			minute: '2-digit'
		})
	);

	const overallAvg = $derived.by(() => {
		const dist = data.distribution;
		const total = dist.reduce((s, d) => s + d.count, 0);
		if (total === 0) return 0;
		return dist.reduce((s, d) => s + d.score * d.count, 0) / total;
	});

	const sentimentLabel = $derived.by(() => {
		if (overallAvg < 1.5) return { text: 'Frustrated', color: 'text-sentiment-1' };
		if (overallAvg < 2.5) return { text: 'Annoyed', color: 'text-sentiment-2' };
		if (overallAvg < 3.5) return { text: 'Neutral', color: 'text-sentiment-3' };
		if (overallAvg < 4.5) return { text: 'Satisfied', color: 'text-sentiment-4' };
		return { text: 'Delighted', color: 'text-sentiment-5' };
	});

	const peakHour = $derived.by(() => {
		if (data.hourly.length === 0) return null;
		const worst = data.hourly.toSorted((a, b) => a.avg_score - b.avg_score)[0];
		const h = worst.hour;
		const ampm = h < 12 ? 'AM' : 'PM';
		const display = h === 0 ? 12 : h > 12 ? h - 12 : h;
		return `${display} ${ampm}`;
	});

	const worstDay = $derived.by(() => {
		if (data.weekday.length === 0) return null;
		const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
		const worst = data.weekday.toSorted((a, b) => a.avg_score - b.avg_score)[0];
		return days[worst.dow];
	});
</script>

<svelte:head>
	<title>cc-sentiment</title>
	<meta name="description" content="AI coding assistant sentiment dashboard" />
</svelte:head>

<div class="min-h-screen bg-bg px-4 py-8 sm:px-6">
	<header class="mx-auto mb-8 max-w-6xl">
		<div class="flex flex-col gap-4 sm:flex-row sm:items-baseline sm:justify-between">
			<div>
				<h1 class="text-2xl font-semibold tracking-tight text-text">cc-sentiment</h1>
				<p class="mt-1 text-sm text-text-dim">AI coding assistant sentiment analysis</p>
			</div>
			<div class="flex items-center gap-4 text-sm text-text-muted">
				<a href="/docs" class="text-accent hover:text-accent-hover transition-colors">API Docs</a>
				<span class="text-text-dim">&middot;</span>
				<span>{data.total_records.toLocaleString()} records</span>
				<span class="text-text-dim">&middot;</span>
				<span>Updated {lastUpdated}</span>
			</div>
		</div>
	</header>

	{#if data.total_records === 0}
		<div class="mx-auto max-w-6xl">
			<div class="flex h-96 flex-col items-center justify-center rounded-xl border border-border bg-bg-card">
				<p class="text-lg text-text-muted">No data yet</p>
				<p class="mt-2 text-sm text-text-dim">Run <code class="rounded bg-bg-hover px-2 py-0.5 font-mono text-accent">cc-sentiment scan --upload</code> to get started</p>
			</div>
		</div>
	{:else}
		<div class="mx-auto mb-6 grid max-w-6xl grid-cols-2 gap-4 sm:grid-cols-4">
			<div class="rounded-xl border border-border bg-bg-card p-4">
				<p class="text-xs font-medium uppercase tracking-wider text-text-dim">Overall</p>
				<p class="mt-1 text-2xl font-semibold {sentimentLabel.color}">{overallAvg.toFixed(1)}</p>
				<p class="text-xs {sentimentLabel.color}">{sentimentLabel.text}</p>
			</div>

			<div class="rounded-xl border border-border bg-bg-card p-4">
				<p class="text-xs font-medium uppercase tracking-wider text-text-dim">Records</p>
				<p class="mt-1 text-2xl font-semibold text-text">{data.total_records.toLocaleString()}</p>
				<p class="text-xs text-text-muted">total scored</p>
			</div>

			<div class="rounded-xl border border-border bg-bg-card p-4">
				<p class="text-xs font-medium uppercase tracking-wider text-text-dim">Worst Hour</p>
				<p class="mt-1 text-2xl font-semibold text-sentiment-1">{peakHour ?? '---'}</p>
				<p class="text-xs text-text-muted">lowest avg score</p>
			</div>

			<div class="rounded-xl border border-border bg-bg-card p-4">
				<p class="text-xs font-medium uppercase tracking-wider text-text-dim">Worst Day</p>
				<p class="mt-1 text-2xl font-semibold text-sentiment-2">{worstDay ?? '---'}</p>
				<p class="text-xs text-text-muted">lowest avg score</p>
			</div>
		</div>

		<main class="mx-auto grid max-w-6xl grid-cols-1 gap-6 lg:grid-cols-2">
			<div class="rounded-xl border border-border bg-bg-card p-6 lg:col-span-2">
				<h2 class="mb-4 text-sm font-medium text-text-muted">Sentiment Over Time</h2>
				<SentimentTimeline data={data.timeline} />
			</div>

			<div class="rounded-xl border border-border bg-bg-card p-6">
				<h2 class="mb-4 text-sm font-medium text-text-muted">Score Distribution</h2>
				<DistributionHistogram data={data.distribution} />
			</div>

			<div class="rounded-xl border border-border bg-bg-card p-6">
				<h2 class="mb-4 text-sm font-medium text-text-muted">By Day of Week</h2>
				<WeekdayBar data={data.weekday} />
			</div>

			<div class="rounded-xl border border-border bg-bg-card p-6 lg:col-span-2">
				<h2 class="mb-4 text-sm font-medium text-text-muted">By Hour of Day</h2>
				<HourlyHeatmap data={data.hourly} />
			</div>
		</main>
	{/if}

	<footer class="mx-auto mt-12 max-w-6xl border-t border-border pt-6 pb-8 text-center text-xs text-text-dim">
		<p>
			<a href="https://github.com/yasyf/cc-sentiment" class="text-accent hover:text-accent-hover transition-colors" target="_blank" rel="noopener">
				cc-sentiment
			</a>
			&mdash; open source AI coding sentiment analysis
		</p>
	</footer>
</div>
